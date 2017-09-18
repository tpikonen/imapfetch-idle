import sys, os.path, tempfile, subprocess, logging
import threading, shlex

sectionkws = ["maildirstore", "imapaccount", "imapstore", "channel", "group"]

# Parse configuration vars from .mbsyncrc, this includes name, server, port
# certfile, user, password, security.
# If channels is given, only return config for them, otherwise return channels
# which have 'INBOX' as their 'Master' store mbox.
def parse(channels=None, mbsyncrc="~/.mbsyncrc"):
    istoresection = None
    istoreconf = {}
    chansection = None
    chanconf = {}
    groupsection = None
    groupconf = {}
    with open(os.path.expanduser(mbsyncrc)) as f:
        for s in f.readlines():
            if s[0] == '#':
                continue
            items = shlex.split(s)
            # Require lines with at least 2 tokens
            if len(items) > 1:
                keyword = items[0].lower()
            else:
                continue
            # The "channel" keyword is a special case, since it's both
            # a section-starting keyword and a value keyword in group section,
            # so we handle the value case first.
            if (keyword == "channel" or keyword == "channels") \
              and groupsection is not None:
                groupconf[groupsection].extend(items[1:])
            elif keyword in sectionkws:
                # FIXME: Also parse imapaccount
                if keyword == "imapstore":
                    istoresection = items[1]
                    istoreconf[istoresection] = {}
                    groupsection = None
                    chansection = None
                elif keyword == "channel" and groupsection is None:
                    chansection = items[1]
                    chanconf[chansection] = {}
                    istoresection = None
                    groupsection = None
                elif keyword == "group":
                    groupsection = items[1]
                    groupconf[groupsection] = []
                    istoresection = None
                    chansection = None
                else:
                    istoresection = None
                    chansection = None
                    groupsection = None
            elif keyword == "host" and istoresection:
                istoreconf[istoresection]["server"] = items[1]
            elif keyword == "port" and istoresection:
                istoreconf[istoresection]["port"] = items[1]
            elif keyword == "user" and istoresection:
                istoreconf[istoresection]["user"] = items[1]
            elif keyword == "pass" and istoresection:
                istoreconf[istoresection]["passwd"] = items[1]
            elif keyword == "passcmd" and istoresection:
                passcmd = items[1][1:] if items[1][0] == '+' else items[1]
                pw = subprocess.check_output(passcmd.split())
                pw = pw.decode()
                pw = pw[:-1] if pw[-1] == '\n' else pw
                istoreconf[istoresection]["passwd"] = pw
            elif keyword == "ssltype" and istoresection:
                ssltype2sec = { "none" : "None",
                                "starttls" : "starttls",
                                "imaps" : "explicit-ssl",
                              }
                istoreconf[istoresection]["security"] = \
                  ssltype2sec[items[1].lower()]
                if not "port" in istoreconf[istoresection].keys():
                    istoreconf[istoresection]["port"] = \
                      993 if items[1].lower() == "imaps" else 143
            elif keyword == "certificatefile" and istoresection:
                istoreconf[istoresection]["certfile"] = items[1]
            elif keyword == "master" and chansection:
                ll = items[1].strip().split(":")
                chanconf[chansection]["master-store"] = ll[1]
                chanconf[chansection]["master-mbox"] = ll[2]
            elif keyword == "slave" and chansection:
                ll = items[1].strip().split(":")
                chanconf[chansection]["slave-store"] = ll[1]
                chanconf[chansection]["slave-mbox"] = ll[2]

    outconf = {}
    # FIXME: Does 'folders' need to be a list?
    if channels is None:
        for k in chanconf.keys():
            if chanconf[k].get("master-mbox", "") != "INBOX":
                continue
            imapstore = chanconf[k]["master-store"]
            istore = istoreconf[imapstore]
            outconf[k] = istore
            outconf[k]["imapstore"] = imapstore
            outconf[k]["folders"] = [chanconf[k].get("master-mbox", "INBOX")]
    else:
        for k in channels:
            imapstore = chanconf[k]["master-store"]
            istore = istoreconf[imapstore]
            outconf[k] = istore
            outconf[k]["imapstore"] = imapstore
            chanconf[k].get("master-mbox", "INBOX")
            outconf[k]["folders"] = [chanconf[k].get("master-mbox", "INBOX")]

    return outconf


# Replace .mbsynrc's PassCmd stanzas in IMAPStore sections with 'pass password',
# where password comes from the passwords dict of the form
# {"imapstore-section-name" : "password" }. Write output to stream 'out'.
def generate(passwords, out, mbsyncrc="~/.mbsyncrc"):
    section = None
    with open(os.path.expanduser(mbsyncrc)) as f:
        for s in f.readlines():
            if s[0] == '#':
                continue
            items = shlex.split(s)
            if len(items) > 0:
                keyword = items[0].lower()
            else:
                out.write(s)
                continue
            if keyword in sectionkws:
                if keyword in ["imapstore", "imapaccount"] and len(items) > 1 \
                  and items[1] in passwords.keys():
                    section = items[1]
                else:
                    section = None
                out.write(s)
                continue
            elif keyword == "passcmd" and section:
                out.write("pass %s\n" % passwords[section])
                continue
            else:
                out.write(s)
        out.close()


# Call mbsync with passwords and extra command line args.
def call_mbsync(conf, params=["-a"]):
    passwords= {}
    for k in conf.keys():
        passwords[conf[k]["imapstore"]] = conf[k]["passwd"]
    tmpdir = tempfile.mkdtemp()
    fifoname = os.path.join(tmpdir, "fifo")
    os.mkfifo(fifoname)
    def conf2fifo():
        # opening a fifo for writing blocks, thus we need this wrapping method
        confout = open(fifoname, mode="w")
        generate(passwords, confout)
        confout.close()
    sys.stdout.flush()
    confthread = threading.Thread(target=conf2fifo)
    confthread.start()

    mbsync = '/usr/bin/mbsync'
    args = [mbsync, "-c", "%s" % fifoname]
    args.extend(params)
    logging.info("Triggering sync: '%s'", (" ".join(args)))
    retval = subprocess.call(args)
    logging.info("Sync finished with exit code {}".format(retval))

    confthread.join()
    os.remove(fifoname)
    os.rmdir(os.path.dirname(fifoname))


if __name__ == '__main__':
    pass
#    conf = parse()
#    print(conf)
