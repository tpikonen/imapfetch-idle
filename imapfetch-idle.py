#!/usr/bin/env python3
# vim:ts=4:sts=4:sw=4:et:tw=79

# Copyright (c) 2014, Clemens Lang
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from threading import *
from queue import *
from OpenSSL import crypto

import os.path
import select
import ssl
import subprocess
import sys
import time
import logging

import imaplib2

import mbsyncrc

TIMEOUT_MINUTES = 30

STARTTLS = "starttls"
EXPLICIT_SSL = "explicit-ssl"


# See http://blog.timstoop.nl/2009/03/11/python-imap-idle-with-imaplib2/
class IMAPSocket():
    def __init__(self, queue, name, server, certfile, user, passwd, directory,
                 security=STARTTLS, port=143):
        self.thread = Thread(target=self.idle)
        self.globalQ = queue
        self.localEv = Event()
        self.deathpill = False
        self.connected = False

        self.name = name
        self.server = server
        self.certfile = certfile
        self.user = user
        self.passwd = passwd
        self.directory = directory
        self.security = security
        self.port = port

    def matchCertificate(self, peercert, host):
        if host != self.server:
            return "Hosts do not match"
        ssl.match_hostname(peercert, self.server)
        return None

    def verifyCertificate(self, peercert, host):
        peerX509 = crypto.load_certificate(crypto.FILETYPE_ASN1,
                                           peercert)
        with open(self.certfile, "r") as cert:
            localX509 = crypto.load_certificate(crypto.FILETYPE_PEM,
                                                cert.read())

        if peerX509.get_subject() != localX509.get_subject():
            return "Subjects don't match"
        if peerX509.digest("sha1") != localX509.digest("sha1"):
            return "Digests don't match"
        return None

    def connect(self):
        try:
            if self.security == STARTTLS:
                self.M = imaplib2.IMAP4(self.server, self.port,
                                        timeout=20)
                self.M.starttls(
                    ca_certs=self.certfile,
                    cert_verify_cb=self.matchCertificate, ssl_version="tls1")
            elif self.security == EXPLICIT_SSL:
                self.M = imaplib2.IMAP4_SSL(
                    self.server, self.port, ca_certs=self.certfile,
                    cert_verify_cb=self.matchCertificate, ssl_version="tls1",
                    timeout=20, debug=0)
            else:
                raise Exception("Unsupported security method. Refusing to go"
                                " unencrypted.")
        except ssl.SSLError as sslE:
            logging.error("%s: SSL Error" % (self.name))
            raise sslE

        try:
            self.M.login(self.user, self.passwd)
        except self.M.error as imapE:
            logging.error("%s: Imap Error" % (self.name))
            raise imapE
        status, msgs = self.M.select(self.directory, readonly=True)
        if status != "OK":
            raise Exception("Could not select mailbox {mailbox}: "
                            "{status} {msgs}".format(
                                mailbox=self.directory,
                                status=status,
                                msgs=msgs))

        self.connected = True

    def start(self):
        self.thread.start()

    def stop(self):
        self.deathpill = True
        self.localEv.set()

    def join(self):
        self.thread.join()

    def idle(self):
        logging.debug("%s: idle start" % self.name)
        while not self.deathpill:
            while not self.connected:
                logging.debug("%s: trying to connect" % self.name)
                try:
                    logging.debug("{}: Thread count: {}".format(self.name,
                        active_count()))
                    self.connect()
                except Exception as e:
                    logging.error("{}: Error connecting to {}:"
                        "{}: {!s}".format(self.name, self.server,
                            self.directory, e))
                    if self.connected:
                        self.M.logout()
                        self.connected = False
                    time.sleep(5*60)

            def callback(args):
                self.localEv.set()

            try:
                # This will return immediately and run IDLE asynchronously.
                self.M.idle(callback=callback)
                logging.debug("{}: Thread count: {}".format(self.name,
                    active_count()))
                logging.info("{}: Idling on {}:{}...".format(self.name,
                    self.server, self.directory))
            except imaplib2.IMAP4.abort as e:
                # "You must call this [logout()] to shut down threads
                # before discarding an instance."
                self.M.logout()
                self.connected = False
                if self.deathpill:
                    return
                logging.error("{}: Connection to {}:{} terminated "
                    "unexpectedly: {!s}".format(self.name, self.directory, e))

            logging.debug("%s: waiting" % self.name)
            # Wait for the IMAP command to complete, or the stop() method being
            # called.
            self.localEv.wait()
            logging.debug("%s: clearing queue" % self.name)
            self.localEv.clear()

            if self.deathpill:
                return

            logging.debug("%s: IDLE wait ended" % self.name)
            try:
                code, resp = self.M.response('IDLE')
                mbox_changed = False if resp[0] == b'TIMEOUT' else True
                logging.debug("%s: IDLE response: %s" % (self.name, str(resp)))
                res = self.M.recent()
                _ = self.M.examine(self.directory)
            except imaplib2.IMAP4.abort as e:
                if self.deathpill:
                    return
                logging.error("{}: Connection to {}:{} terminated during IDLE"
                    " handling: {!s}".format(self.name, self.server,
                        self.directory, e))
                self.M.logout()
                self.connected = False

            if mbox_changed:
                Nnew = sum(map(lambda x: 1 if (x and x != '0') else 0,
                    res[1])) if res else 0
                logging.debug("{}: Found {:d} new mails on {}:{}"\
                    .format(self.name, Nnew, self.server, self.directory))
                self.globalQ.put((self.name, self.directory))
            else:
                logging.debug("%s: IDLE timeout" % self.name)
            logging.debug("%s: at the end of idle loop" % self.name)

if __name__ == '__main__':
    #enabled_channels = ["enabled-inbox"]
    enabled_channels = [] # empty list == all channels enabled
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s:%(levelname)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    conf = mbsyncrc.parse()

    q = Queue()

    sockets = []

    mbsyncrc.call_mbsync(conf)

    try:
        for imap in conf.keys():
            if len(enabled_channels) > 0 and imap not in enabled_channels:
                continue
            d = conf[imap].copy()
            d.pop("folders")
            d.pop("imapstore")
            for f in conf[imap]["folders"]:
                d["directory"] = f
                try:
                    sockets.append(IMAPSocket(q, imap, **d))
                except Exception as e:
                    logging.error("Error connecting to account {!s}/{!s}: {}"
                        .format(d["server"], d["directory"], e))

        if len(sockets) == 0:
            exit(0)

        for sock in sockets:
            logging.debug("Thread count: {}".format(active_count()))
            sock.start()
            time.sleep(0.25)

        items = set()
        while True:

            items.clear()
            try:
                # wait for an event, but at most TIMEOUT_MINUTES
                item = q.get(timeout=TIMEOUT_MINUTES * 60)
                items.add(item)
                q.task_done()

                channel, folder = item
                logging.info("Mail on {}, mailbox {}."
                    " Waiting...".format(channel, folder))

                # Wait for three seconds to see if we can consolidate
                try:
                    while True:
                        item = q.get(timeout=1)
                        items.add(item)
                        q.task_done()
                        channel, folder = item
                        logging.info("Additional activity on {}, mailbox"
                            " {}.".format(channel, folder))
                except Empty as qee:
                    pass
            except Empty as qee:
                logging.info("Timeout reached.")
                pass

            args = []
            for item in items:
                channel, folder = item
                args.append("{}".format(channel))
            if len(items) == 0:
                args.append("-a")
            mbsyncrc.call_mbsync(conf, args)
            logging.debug("Thread count: {}".format(active_count()))
    except KeyboardInterrupt as ki:
        logging.info("^C received, shutting down...")
    finally:
        for sock in sockets:
            sock.stop()
        for sock in sockets:
            sock.join()
