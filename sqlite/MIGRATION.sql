-- This migrates the old, handwritten database to the first revision managed by Alembic.
-- Please run `flask db upgrade` as soon as possible!

------------
-- Config --
------------

-- As config now uses a DATETIME, we will delete the old table entirely.
DROP TABLE config;

CREATE TABLE config (
    network INTEGER NOT NULL,
    backend_uptime DATETIME,
    PRIMARY KEY (network)
);

-- Provide default uptime for our two defined networks.
INSERT INTO config VALUES (0, NULL);
INSERT INTO config VALUES (1, NULL);

-------------
-- Friends --
-------------
CREATE TABLE new_friends (
     friend_code VARCHAR NOT NULL,
     network INTEGER NOT NULL,
     online BOOLEAN NOT NULL,
     title_id VARCHAR NOT NULL,
     upd_id VARCHAR NOT NULL,
     last_accessed BIGINT NOT NULL,
     account_creation BIGINT NOT NULL,
     username VARCHAR,
     message VARCHAR,
     mii VARCHAR,
     joinable BOOLEAN,
     game_description VARCHAR,
     last_online BIGINT NOT NULL,
     favorite_game BIGINT NOT NULL,
     PRIMARY KEY (friend_code),
     UNIQUE (friend_code)
);

-- The primary difference between these two tables are the column names.
-- `jeuFavori` was renamed to its English version, `favorite_game`.
-- `friend_code` is no longer unique.
INSERT INTO new_friends
    (friend_code, network, online, title_id,
     upd_id, last_accessed, account_creation, username,
     message, mii, joinable, game_description,
     last_online, favorite_game)
SELECT
    friendCode, network, online, titleID,
    updID, lastAccessed, accountCreation,
    username, message, mii, joinable,
    gameDescription, lastOnline, jeuFavori
FROM friends;

-- Swap out.
DROP TABLE friends;
ALTER TABLE new_friends RENAME TO friends;

--------------------
-- discordFriends --
--------------------

-- This table was renamed to discordFriends.
CREATE TABLE discord_friends (
     id BIGINT NOT NULL,
     friend_code VARCHAR NOT NULL,
     network INTEGER NOT NULL,
     active BOOLEAN NOT NULL,
     PRIMARY KEY (id, friend_code)
);

-- Note that we use `friend_code` instead of `friendCode`.
INSERT INTO discord_friends
    (id, friend_code, network, active)
SELECT
    ID, friendCode, network, active
FROM discordFriends;

-- Remove our old, poorly-named table.
DROP TABLE discordFriends;

-------------
-- discord --
-------------
CREATE TABLE new_discord (
     id BIGINT NOT NULL,
     refresh VARCHAR NOT NULL,
     bearer VARCHAR NOT NULL,
     session VARCHAR,
     token VARCHAR,
     last_accessed BIGINT NOT NULL,
     generation_date BIGINT NOT NULL,
     show_profile_button BOOLEAN NOT NULL,
     show_small_image BOOLEAN NOT NULL,
     PRIMARY KEY (id),
     UNIQUE (id),
     UNIQUE (token)
);

INSERT INTO new_discord
    (id, refresh, bearer, session,
     token, last_accessed, generation_date,
     show_profile_button, show_small_image)
SELECT
    ID, refresh, bearer, session, token, lastAccessed,
    generationDate, showProfileButton, showSmallImage
FROM discord;


-- Swap them out.
DROP TABLE discord;
ALTER TABLE new_discord RENAME TO discord;

-------------
-- Alembic --
-------------

-- Lastly, spoof having our initial Alembic revision.
CREATE TABLE alembic_version (
     version_num VARCHAR(32) NOT NULL
         CONSTRAINT alembic_version_pkc
             PRIMARY KEY
);
INSERT INTO alembic_version VALUES ('f2475122ee84');
