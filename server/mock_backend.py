#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Mock backend data for the Field & Flower flower-shop demo.

This is the file to edit when customizing the demo for your own hackathon
project: swap the catalog, add or remove "known customer" phone numbers, or
replace the dicts entirely with calls to a real backend (database, REST API,
etc.) from inside the tool functions in ``bot.py``.

Both lookups are case-insensitive on the key side in ``bot.py`` — bouquet
names are lowercased before lookup, and phone numbers should be stored in
E.164 format (e.g. ``+14155551234``) to match Twilio's ``from_number``.

Each bouquet carries:
    price (USD), description, in_stock (bool), occasions (list of lowercase
    strings the LLM can filter on), on_special (bool — used by the "any
    deals?" path).
"""

BOUQUETS = {
    "spring sunshine": {
        "price": 45.00,
        "description": "Yellow tulips and daffodils",
        "in_stock": True,
        "occasions": ["birthday", "thank you", "get well", "mother's day", "spring"],
        "on_special": False,
    },
    "rose romance": {
        "price": 65.00,
        "description": "A dozen red roses with baby's breath",
        "in_stock": True,
        "occasions": ["valentine's day", "anniversary", "romance", "date night"],
        "on_special": False,
    },
    "wildflower medley": {
        "price": 38.00,
        "description": "Mixed seasonal wildflowers",
        "in_stock": True,
        "occasions": ["birthday", "thank you", "just because", "housewarming"],
        "on_special": True,
    },
    "lily elegance": {
        "price": 55.00,
        "description": "White lilies and greenery",
        "in_stock": False,
        "occasions": ["sympathy", "funeral", "remembrance"],
        "on_special": False,
    },
    "succulent garden": {
        "price": 42.00,
        "description": "Assorted succulents in a ceramic pot",
        "in_stock": True,
        "occasions": ["housewarming", "office", "thank you", "low maintenance"],
        "on_special": False,
    },
    "mother's day pastels": {
        "price": 58.00,
        "description": "Pink peonies, lavender, and white roses",
        "in_stock": True,
        "occasions": ["mother's day", "birthday", "thank you"],
        "on_special": False,
    },
    "birthday brights": {
        "price": 48.00,
        "description": "Sunflowers, gerbera daisies, and orange roses",
        "in_stock": True,
        "occasions": ["birthday", "congratulations", "thank you"],
        "on_special": True,
    },
    "sympathy whites": {
        "price": 70.00,
        "description": "White lilies, roses, and chrysanthemums",
        "in_stock": True,
        "occasions": ["sympathy", "funeral", "remembrance", "condolences"],
        "on_special": False,
    },
    "anniversary blush": {
        "price": 75.00,
        "description": "Two dozen pink roses with eucalyptus",
        "in_stock": False,
        "occasions": ["anniversary", "valentine's day", "romance", "engagement"],
        "on_special": False,
    },
    "garden party": {
        "price": 52.00,
        "description": "Hydrangeas, snapdragons, and stock",
        "in_stock": True,
        "occasions": ["wedding", "shower", "birthday", "thank you"],
        "on_special": False,
    },
    "autumn harvest": {
        "price": 46.00,
        "description": "Sunflowers, mums, and fall foliage",
        "in_stock": True,
        "occasions": ["fall", "thanksgiving", "autumn", "halloween", "thank you"],
        "on_special": True,
    },
    "winter pine": {
        "price": 54.00,
        "description": "White roses, pine, cedar, and eucalyptus",
        "in_stock": True,
        "occasions": ["winter", "christmas", "holiday", "new year"],
        "on_special": False,
    },
    "new arrival": {
        "price": 44.00,
        "description": "Soft pink and white gerberas with daisies",
        "in_stock": True,
        "occasions": ["new baby", "baby shower", "congratulations"],
        "on_special": False,
    },
    "graduation gold": {
        "price": 48.00,
        "description": "Sunflowers, yellow roses, and billy balls",
        "in_stock": True,
        "occasions": ["graduation", "congratulations", "achievement"],
        "on_special": False,
    },
    "tulip tower": {
        "price": 40.00,
        "description": "Assorted spring tulips",
        "in_stock": True,
        "occasions": ["spring", "easter", "just because", "thinking of you"],
        "on_special": False,
    },
}

# Add your own number here if you want to test the bot with a known customer
KNOWN_CUSTOMERS = {
    "+14155551234": {"name": "Alex", "last_order": "rose romance"},
    "+14155555678": {"name": "Jordan", "last_order": "wildflower medley"},
}
