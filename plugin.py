###
# Copyright (c) 2015, Bogdano Arendartchuk
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

from .telegram import TelegramBot
import supybot.callbacks as callbacks
import supybot.ircmsgs as ircmsgs
from supybot.ircutils import mircColor, canonicalColor

import traceback
import threading
import time

import importlib
from . import telegram
importlib.reload(telegram)


class TelegramBridge(callbacks.Plugin):
    """Add the help for "@plugin help TelegramBridge" here
    This should describe *how* to use this plugin."""

    _pipe = None

    def __init__(self, irc):
        super(TelegramBridge, self).__init__(irc)
        self.log.debug("TelegramBridge initializing")
        self._tgToken = self.registryValue("tgToken")
        self.telegram_loop_run = True
        try:
            self._tgId = int(self._tgToken.split(":", 1)[0])
        except ValueError:
            self.log.error("failed to parse tgToken, please check it is in "
                           "the <ID>:<COOKIE> format")
        self._tgTimeout = self.registryValue("tgTimeout")
        self._tgIrc = irc
        self._tg = TelegramBot(self._tgToken)
        self._start_telegram_loop()

    def _feed_to_supybot(self, channel, author, text):
        # Clean up text if incoming command is from bot keyword
        from re import sub
        text = sub(r"^/([^\s@]+)(?:@\w+)", r"|\1", text)
        new_msg = ircmsgs.privmsg(channel, text)
        new_msg.prefix = self._tgIrc.prefix
        new_msg.tag("from_telegram")
        new_msg.nick = author
        self.log.debug("feeding back to supybot: %s", new_msg)
        self._tgIrc.feedMsg(new_msg)

    @staticmethod
    def _tg_user_repr(user):
        user_id = user.get("id", "??")
        last_name = user.get("last_name", "")
        name = user.get("first_name", str(user_id)) + last_name
        chosen = user.get("username", name)
        return user_id, chosen

    @staticmethod
    def _tg_repr_location(location):
        template = ("<location http://www.google.com/maps/place/"
                    "{0},{1}/@{0},{1},17z>")
        text = template.format(location.get("latitude"),
                               location.get("longitude"))
        return text

    @staticmethod
    def _tg_repr_contact(contact):
        template = "<contact {} {} {}>"
        text = template.format(contact.get("first_name"),
                               contact.get("last_name"),
                               contact.get("phone_number"))
        return text

    @staticmethod
    def _tg_repr_non_text(message):
        text = ""
        for type in ("photo", "video", "audio", "sticker", "contact",
                     "location", "venue", "voice", "game", "document"):
            object = message.get(type)
            if object:
                if type == "sticker":
                    text = "<sticker {}>".format(object.get("emoji"))
                elif type == "location":
                    text = TelegramBridge._tg_repr_location(object)
                elif type == "contact":
                    text = TelegramBridge._tg_repr_contact(object)
                else:
                    text = "<{}>".format(type)
                break
        return text

    @staticmethod
    def _tg_repr_message(message):
        text = message.get("text")
        if not text:
            text = TelegramBridge._tg_repr_non_text(message)
        return text

    def _get_channel_from_chat(self, message):
        chat_ids = {self.registryValue("tgChatId", ch):
                    ch for ch in self._tgIrc.state.channels}
        chat_id = message.get("chat")
        if not chat_id:
            self.log.warning("Malformed Telegram message")
            return None

        chat_id = chat_id.get("id")
        channel = chat_ids.get(chat_id, None)
        if channel:
            self.log.debug("Got message from Telegram chat %s, relaying "
                           "to channel %s", chat_id, channel)
        else:
            self.log.info("Got message from unknown Telegram group: %s",
                          chat_id)
        return channel

    def _tg_handle_message(self, message):
        channel = self._get_channel_from_chat(message)
        if not channel:
            return

        text = self._tg_repr_message(message)
        user = message.get("from")
        user_id, author = self._tg_user_repr(user)
        if user_id == self._tgId:
            return  # Ignore messages from myself

        for line in text.splitlines():
            color = canonicalColor(author)
            irc_text = "%s> %s" % (mircColor(author, *color), line)
            self._send_irc_message(channel, irc_text)
            self._feed_to_supybot(channel, author, line)

    def _telegram_discard_previous_updates(self):
        update_id = None
        for update_id, update in self._tg.updates():
            pass
        all(self._tg.updates(state=update_id))

    def _telegram_loop(self):
        self._telegram_discard_previous_updates()
        while self.telegram_loop_run:
            try:
                for message in self._tg.updates_loop(self._tgTimeout):
                    self._tg_handle_message(message)
            except Exception as e:
                self.log.debug("%s", traceback.format_exc())
                self.log.critical("%s", str(e))
            time.sleep(1)

    def _start_telegram_loop(self):
        self.telegram_loop_run = True
        t = threading.Thread(target=self._telegram_loop)
        t.setDaemon(True)
        t.start()

    def _send_to_chat(self, text, chatId):
        self._tg.send_message(chatId, text)

    def _send_irc_message(self, channel, text):
        new_msg = ircmsgs.privmsg(channel, text)
        new_msg.tag("from_telegram")
        self._tgIrc.queueMsg(new_msg)

    def doPrivmsg(self, irc, msg):
        irc = callbacks.SimpleProxy(irc, msg)
        channel = msg.args[0]
        if (not msg.isError and channel in irc.state.channels
                and not msg.from_telegram):
            chat_id = self.registryValue("tgChatId", channel)
            if not chat_id or chat_id == 0:
                self.log.debug("TelegramBridge not configured for channel %s",
                               channel)
                return

            text = msg.args[1]
            if ircmsgs.isAction(msg):
                text = ircmsgs.unAction(msg)
                line = "* %s %s" % (msg.nick, text)
            else:
                line = "%s> %s" % (msg.nick, text)
            self._send_to_chat(line, chat_id)

    def doTopic(self, irc, msg):
        if len(msg.args) == 1:
            return
        channel = msg.args[0]
        topic = msg.args[1]
        line = u"%s: %s" % (channel, topic)
        self._send_to_chat(line)

    def outFilter(self, irc, msg):
        if msg.command == "PRIVMSG" and not msg.from_telegram:
            self.doPrivmsg(irc, msg)
        return msg

    def die(self):
        self.telegram_loop_run = False


Class = TelegramBridge


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
