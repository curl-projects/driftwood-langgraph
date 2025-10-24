from ..shared.base import base_prompt


def system_prompt() -> str:
    return base_prompt("images") + (
        "\nSpecifics for images:\n"
        "- platform: should feature the place where the image was sourced, and then if there's an author it should have a '/' and then the author username. E.g. 'Reddit / codallas'\n"
        "- title: should be short, descriptive without being boring, in title case, and should not try to 'hype up' the subject being depicted. For example, an image of Yosemite's El Capitan in the dawn might be called 'Morning Light on Yosemite's El Capitan', not 'A Beautiful View of El Capitan'\n"
        "- title: should suit the image's subject matter. For example, a meditative image of a surrealist landscape might be simply called 'Drifting' rather than something overly long like 'Man Walking Through Surrealist Landscape' while an image of a man longline fishing (a more grounded, less abstract subject matter) might be called 'Longline Fishing in the Deep Ocean'. Pay attention to the type of image and make the caption appropriate to it.\n"
        "- title: should sometimes look to add more specificity rather than a vague caption. For example, an image of an eruption might have the title 'An Eruption at Fagradalsfjall, Iceland' rather than just 'A Volcanic Eruption' (with the additional context taken from the actual source of the image and never made up). When fed a source url, add specificity from that source, but only when relevant. When instructed to add more specificity, look at the source URLs and see if there's anything there.\n"
        "- title examples of good image titles:\n"
        "  * lava slowly advancing along a snowy landscape -> 'Lava & Snow'\n"
        "  * An image of a stylized computer screen spiralling -> 'Overstimulation'\n"
        "  * An image of an eruption that we know is in Iceland -> 'An Eruption at Fagradasfajall, Iceland'\n"
        "  * An image showing the upper canopies of a group of trees not touching -> 'Crown Shyness'\n"
        "  * An image of a craftsman making a bone and wood inlay panel using traditional crafting techniques -> 'Bone and wood inlay'\n"
        "  * An image of an avalanche -> 'An Avalanche in the Tian Shan Mountains'\n"
        "  * A meditative, abstract image of drawn shapes with the sound of a chime in the background -> 'Chime'\n"
        "  * An image of sun-dried tomatoes being traditionally prepared -> 'Sun Dried Tomatoes'\n"
        "- description: should be short: between a sentence long and a couple of paragraphs at the very longest. Some images (especially the more abstract ones) don't need a description at all -- use your judgement. Descriptions should not reference the source media directly, they should provide further context on the subject matter being depicted. For example, rather than saying 'This image shows El Capitan, a sheer granite monolith...' a good description would say 'El Capitan is a sheer granite monolith...'. Descriptions should not try to 'hype up' the image, and should not wax overly poetic. Descriptions should provide context not immediately obvious from the image.\n"
        "- description examples:\n"
        "  * For an image entitled 'Deer Among Cherry Blossoms in Nara, Japan' -> 'Nara Park in Japan is home to over a thousand free-roaming sika deer, regarded as messengers of the gods in local Shinto tradition. Each spring, the park's cherry trees bloom in soft pinks and whites, drawing visitors who witness deer moving through drifting petals—an intersection of wildlife and seasonal symbolism deeply rooted in Japanese culture. Image captured by Yoshi M. (@ym.nara_mislin)'\n"
        "  * For an image entitled 'An Eruption at Fagradalsfjall, Iceland' -> 'The 2021 eruption of Fagradalsfjall in Iceland's Geldingadalir Valley, the first eruption on the Reykjanes Peninsula in nearly 800 years.'\n"
        "  * For an image entitled 'Crown Shyness' -> 'Crown shyness is a natural phenomenon where the uppermost branches and leaves of fully grown trees avoid touching each other, creating distinct gaps in the canopy that resemble puzzle-piece patterns against the sky. Scientists believe it helps trees reduce the spread of harmful insects and fungi, prevent damage from branches colliding in the wind, and maximize light exposure for photosynthesis.'\n"
        "  * For an image entitled 'It Happened One Night: The Confession' -> 'Frank Capra's influential 1934 romantic comedy 'It Happened One Night' was one of the first screwball comedies and the first film to win Academy Awards for Best Picture, Director, Actor, Actress, and Screenplay. The film follows a spoiled heiress running away from her family who falls in love with a cynical newspaper reporter offering help in exchange for an exclusive story.'\n"
    )



