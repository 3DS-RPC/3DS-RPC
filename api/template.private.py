# Created by Deltaion Lee (MCMi460) & Preloading on Github
# This is my private file, but with all of the dangerous stuff removed
# You can use it as a template for yours

###### NINTENDO INFO ##########

NINTENDO_SERIAL_NUMBER:str = "" # Serial number on console minus the last digit
NINTENDO_MAC_ADDRESS:str = "" # Console MAC address (see WiFi settings), all lowercase with no colons
NINTENDO_DEVICE_CERT:bytes = bytes.fromhex("") # Unique console certificate. Get from sniffing traffic or from LocalSeedFriendCode
NINTENDO_DEVICE_NAME:str = "" # Doesn't matter

# 3DS does NOT send NEX credentials over NASC
# They are generated once when the account is created and stored on the device
# Homebrew like https://github.com/Stary2001/nex-dissector/tree/master/get_3ds_pid_password
# can be used to dump the PID and password
# You must dump the nex keys for each network.

NINTENDO_PID:int = 0
NINTENDO_NEX_PASSWORD:str = ''

NINTENDO_PID_HMAC:str = "" # Sniff console traffic or dump from friends title save (bytes 66-84)

NINTENDO_REGION:int = 1 # USA
NINTENDO_LANGUAGE:int = 1 # English

##### PRETENDO INFO #######

PRETENDO_SERIAL_NUMBER:str = "" # Serial number on console minus the last digit
PRETENDO_MAC_ADDRESS:str = "" # Console MAC address (see WiFi settings), all lowercase with no colons
PRETENDO_DEVICE_CERT:bytes = bytes.fromhex("") # Unique console certificate. Get from sniffing traffic or from LocalSeedFriendCode
PRETENDO_DEVICE_NAME:str = "" # Doesn't matter

# 3DS does NOT send NEX credentials over NASC
# They are generated once when the account is created and stored on the device
# Homebrew like https://github.com/Stary2001/nex-dissector/tree/master/get_3ds_pid_password
# can be used to dump the PID and password
# You must dump the nex keys for each network.

PRETENDO_PID:int = 0
PRETENDO_NEX_PASSWORD:str = ''

PRETENDO_PID_HMAC:str = "" # Sniff console traffic or dump from friends title save (bytes 66-84)

PRETENDO_REGION:int = 1 # USA
PRETENDO_LANGUAGE:int = 1 # English

#########################
# Now, Discord secrets! #
# Keep in mind that you #
# will need the scope:  #
# activities.write in   #
# order to replicate    #
# this step.            #
CLIENT_ID:int = 0 # Taken from OAuth2 page
CLIENT_SECRET:str = "" # Taken from OAuth2 page
DISCORD_SCOPE:str = "activities.write" # OAuth2 scope(s), space-separated if multiple
DISCORD_WEBHOOK_URL:str = ""

#### SERVER-SPECIFIC ####
# Finally, we're grabbing #
# server-specific things. #
# This mostly only        #
# pertains to the domain. #
# See format below  \/    #
HOST:str = ""
# http(s)://subdomain.domain.extension
# No ending slash!

### DATABASE-SPECIFIC ###
# The URL of the database to connect to.
#
# For example, with SQLite3, you would connect to the following:
#    sqlite:////home/xyz/my_database.db
# With MySQL, you would use the following:
#     mysql://DB_USERNAME:DB_PASSWORD@DB_HOST/DBNAME
# SQLAlchemy supports many other databases.
# Refer to https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls for format.
DB_URL:str = "sqlite:///./sqlite/fcLibrary.db"
