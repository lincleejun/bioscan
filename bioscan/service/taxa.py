"""The taxa the pipeline knows, in one place: gate classes and their prompts (SigLIP2), detector
words per animal class (OWLv2), which class the crop gate may promote a box to. Name lists per
kind live in names.LISTS. Adding a class (say reptiles to species) starts here.
"""
from __future__ import annotations

# Order matters: siglip2.softmax_gate zips it onto the class matrix rows.
GATE_CLASSES = ("bird", "mammal", "other_animal", "person", "none")
GATE_PROMPTS: dict[str, list[str]] = {
    "bird": ["a photo of a bird.", "a bird perched on a branch.", "a bird flying.", "a small bird.",
             "a duck, gull, hawk, owl or songbird."],
    "mammal": ["a photo of a mammal.", "a deer, bear, seal, whale, squirrel or fox.", "a wild mammal.", "a dog or cat."],
    "other_animal": ["a photo of a reptile or amphibian.", "a lizard, snake, turtle or frog.", "a fish.",
                     "an insect, butterfly or spider."],
    "person": ["a photo of a person.", "a person standing.", "people walking.", "a portrait of a man or woman.",
               "a painting of a person."],
    "none": ["a landscape with mountains, sea or sky.", "a sunset.", "leaves, trees or flowers.",
             "a building or street.", "a plate of food.", "an object or artifact.", "snow and ice.",
             "a photo with no animal in it.", "a tree trunk, bark or forest.", "a rock face, cliff or boulder.",
             "a glacier, snow or ice field.", "a painting, mural or artwork.", "a statue or sculpture.",
             "a city, street or building."],
}

NOT_ANIMAL = ("person", "none")          # a crop gate this sure of these vetoes a box (rules.VETO)
PROMOTE_TO = "bird"                      # the crop gate may turn any other animal box into this kind
# Kinds whose name lists compete in the kind check (pipeline): a box of one of these kinds moves to
# whichever of their lists holds most of its species evidence. A list joins by adding its kind
# here; an all-taxa list must not (it overlaps every other list).
KIND_CHECK = ("bird", "mammal")

# gate class -> {prompt: first-pass floor}
VOCAB: dict[str, dict[str, float]] = {
    "bird": {"a bird": 0.3},
    "mammal": {"a mammal": 0.1, "an animal": 0.1, "a wild animal": 0.1,
               **{f"a {n}": 0.2 for n in ("deer", "bear", "seal", "sea lion", "whale", "moose", "caribou",
                                          "sea otter", "squirrel", "fox", "coyote", "mountain goat", "rabbit",
                                          "elk", "bison", "marmot",
                                          # large carnivores and brush mammals the golden set missed
                                          "black bear", "grizzly bear", "mountain lion", "cougar", "bobcat",
                                          "lynx", "wolf", "wild cat", "raccoon", "skunk", "badger", "bighorn sheep",
                                          "pronghorn", "wild pig", "beaver", "chipmunk")},
               "an opossum": 0.2, "an otter": 0.2},
    "other_animal": {"an animal": 0.1, "a reptile": 0.15, "a lizard": 0.2, "a snake": 0.2, "a turtle": 0.2,
                     "a frog": 0.2, "a fish": 0.2, "an insect": 0.15, "a butterfly": 0.2, "a spider": 0.2},
}

ANIMALS = tuple(VOCAB)                   # gate classes that send a frame to the detector
