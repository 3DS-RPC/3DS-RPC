from enum import IntEnum
from api.public import NINTENDO_BOT_FC, PRETENDO_BOT_FC


class InvalidNetworkError(Exception):
    pass


class NetworkType(IntEnum):
    """Selectable network types."""
    NINTENDO = 0
    PRETENDO = 1

    def friend_code(self) -> str:
        """Returns the configured friend code for this network type."""
        match self:
            case self.NINTENDO:
                return NINTENDO_BOT_FC
            case self.PRETENDO:
                return PRETENDO_BOT_FC

        # Default to Nintendo.
        return NINTENDO_BOT_FC

    def column_name(self) -> str:
        """Returns the database column name for this network type."""
        match self:
            case self.NINTENDO:
                return "nintendo_friends"
            case self.PRETENDO:
                return "pretendo_friends"

        # Default to Nintendo.
        return "nintendo_friends"

    def lower_name(self) -> str:
        """Returns a lowercase name of this enum member's name for API compatibility."""
        return self.name.lower()


def name_to_network_type(network_name: str) -> NetworkType:
    # Assume Nintendo Network as a fallback.
    if network_name is None:
        return NetworkType.NINTENDO

    try:
        # All enum members are uppercase, so ensure we are, too.
        return NetworkType[network_name.upper()]
    except:
        return NetworkType.NINTENDO
