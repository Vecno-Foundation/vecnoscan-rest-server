import os

NETWORK_TYPE = os.getenv("NETWORK_TYPE", "mainnet").lower()

match NETWORK_TYPE:
    case "mainnet":
        address_prefix = "vecno"
        address_example = "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e"
    case "testnet":
        address_prefix = "vecnotest"
        address_example = "vecnotest:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e"
    case "simnet":
        address_prefix = "vecnosim"
        address_example = "vecnosim:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e"
    case "devnet":
        address_prefix = "vecnodev"
        address_example = "vecnodev:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e"
    case _:
        raise ValueError(f"Network type {NETWORK_TYPE} not supported.")

ADDRESS_PREFIX = address_prefix
ADDRESS_EXAMPLE = address_example

REGEX_VECNO_ADDRESS = "^" + ADDRESS_PREFIX + ":[a-z0-9]{61,63}$"
