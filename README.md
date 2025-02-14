dccbot
========

dccbot is a simple irc bot written in python with aiohttp and irc.py.

Features
--------

*   join channels
*   send messages to channels or users
*   part channels
*   support for dcc connections
*   support for sending and receiving files over dcc

Usage
-----

### Installation

    pip install dccbot

### Running

    dccbot

### Configuration

The bot can be configured by creating a `config.json` file in the current working
directory. The configuration file should contain a json object with the following
keys:

*   `servers`: a list of servers the bot should connect to. Each server is an
    object with the following keys:
    *   `host`: the hostname of the irc server
    *   `port`: the port number to connect to
    *   `password`: the password to use when connecting to the server (optional)
    *   `nick`: the nickname to use when connecting to the server (optional)
    *   `channels`: a list of channels to join after connecting to the server
*   `dcc`: a list of dcc connections to accept. Each connection is an object with
    the following keys:
    *   `host`: the hostname of the user to accept dcc connections from
    *   `port`: the port number to listen for dcc connections on
    *   `nick`: the nickname of the user to accept dcc connections from (optional)
    *   `password`: the password to use when accepting dcc connections (optional)

### API

The bot can be controlled using a simple web interface. The web interface is
available at `http://localhost:8080/` by default.

*   `POST /join`: join a channel
*   `POST /part`: part a channel
*   `POST /msg`: send a message to a channel or user
*   `POST /shutdown`: shutdown the bot
*   `POST /update_server`: update a server configuration
*   `POST /remove_server`: remove a server configuration
