dccbot
========

dccbot is a simple irc bot written in python with aiohttp and irc.py.

Features
--------

*   join channels
*   send messages to channels or users
*   part channels
*   support for dcc connections
*   support for receiving files over dcc

Usage
-----

### Configuration

The bot can be configured by creating a `config.json` file in the current working
directory. The configuration file should contain a json object with the following
keys:

*   `servers`: a list of servers the bot should connect to. Each server is an
    object with the following keys:
    *   `nick`: the nickname to use when connecting to the server
    *   `nickserv_password`: the password to use when connecting to the server, optional
    *   `use_tls`: a boolean indicating whether to use tls when connecting to the
        server
    *   `random_nick`: a boolean indicating whether to use a random nickname when
        connecting to the server
*   `download_path`: the directory where the bot should download files
*   `allowed_mimetypes`: a list of mimetypes the bot should allow to be sent
    over dcc
*   `max_file_size`: the maximum size of a file to be sent over dcc
*   `channel_idle_timeout`: the number of seconds a channel can be idle before
    the bot will part the channel
 *  `server_idle_timeout`: the number of seconds a server can be idle before
    the bot will disconnect from the server


### API

The bot can be controlled using a simple web interface. The web interface is
available at `http://localhost:8080/` by default.

*   `POST /join`: join a channel
*   `POST /part`: part a channel
*   `POST /msg`: send a message to a channel or user
*   `POST /shutdown`: shutdown the bot

