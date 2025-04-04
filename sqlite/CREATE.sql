-- This brings us to our initial Alembic revision.
CREATE TABLE alembic_version (
     version_num VARCHAR(32) NOT NULL
         CONSTRAINT alembic_version_pkc
             PRIMARY KEY
);
INSERT INTO alembic_version VALUES ('f2475122ee84');

CREATE TABLE config (
    network INTEGER NOT NULL,
    backend_uptime DATETIME,
    PRIMARY KEY (network)
);

-- Provide default uptime for our two defined networks.
INSERT INTO config VALUES (0, NULL);
INSERT INTO config VALUES (1, NULL);

CREATE TABLE friends (
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
CREATE TABLE discord_friends (
     id BIGINT NOT NULL,
     friend_code VARCHAR NOT NULL,
     network INTEGER NOT NULL,
     active BOOLEAN NOT NULL,
     PRIMARY KEY (id, friend_code)
);

CREATE TABLE discord (
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
