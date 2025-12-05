from __future__ import annotations

from typing import Optional, Dict, Any, List

import time
import logging

from ..generate_image import generate_image
from .base import Contract

logger = logging.getLogger(__name__)


class GeneratedMediaContract(Contract):
    name = "generated_media"

    async def collect(
        self, 
        *, 
        url: Optional[str], 
        urls: Optional[List[str]], 
        field_id: Optional[str], 
        form_values: Optional[Dict[str, Any]] = None, 
        schema_doc: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        start = time.time()
        out: Dict[str, Any] = {"debug": {"steps": []}}
        
        try:
            # Get generation config from schema
            if not schema_doc or not field_id:
                logger.warning("Generated media contract missing schema_doc or field_id")
                return out
                
            field_schema = (schema_doc.get("schema", {})
                          .get("properties", {})
                          .get(field_id, {}))
            
            gen_config = field_schema.get("x-generation", {})
            if not gen_config:
                logger.warning(f"No x-generation config found for field {field_id}")
                return out
            
            # Template interpolation using form values
            template = gen_config.get("promptTemplate", "Generate image for {word}")
            try:
                if form_values:
                    prompt = template.format(**form_values)
                else:
                    prompt = template
            except KeyError as e:
                logger.warning(f"Template interpolation failed: {e}, using template as-is")
                prompt = template
            
            # Generation options
            options = {
                "fieldId": field_id,
                "prompt": prompt,
            }
            
            # Add optional generation parameters
            if gen_config.get("aspect"):
                options["aspect"] = gen_config["aspect"]
            if gen_config.get("style"):
                options["style"] = gen_config["style"] 
            if gen_config.get("negativePrompt"):
                options["negativePrompt"] = gen_config["negativePrompt"]
            if gen_config.get("width"):
                options["width"] = gen_config["width"]
            if gen_config.get("height"):
                options["height"] = gen_config["height"]
            
            # Call the generate_image tool
            result = await generate_image(**options)
            
            # Return in contract format
            if result.get("ok"):
                out["media"] = result.get("attachments", [])
                out["generation"] = {
                    "prompt": prompt,
                    "template": template, 
                    "config": gen_config,
                    "success": True
                }
            else:
                logger.error(f"Image generation failed: {result.get('error', 'Unknown error')}")
                out["generation"] = {
                    "prompt": prompt,
                    "template": template,
                    "config": gen_config, 
                    "success": False,
                    "error": result.get("error")
                }
                
        except Exception as e:
            logger.error(f"Generated media contract error: {e}")
            out["generation"] = {
                "success": False,
                "error": {"code": "exception", "message": str(e)}
            }
        
        out.setdefault("timings", {})["generatedMediaMs"] = int((time.time() - start) * 1000)
        return out


