import sys, os.path, tempfile, subprocess
import threading, shlex

sectionkws = ["maildirstore", "imapaccount", "imapstore", "channel", "group"]
imapkws = ["imapstore", "imapaccount"]

# Parse configuration vars from .mbsyncrc, this includes name, server, port
# certfile, user, password, security.
# Only return IMAP config sections whose name is in mboxes.
def parse(mboxes=None, mbsyncrc="~/.mbsyncrc"):
    imaps = mboxes.keys() if type(mboxes) == dict else mboxes
    conf = {}
    section = None
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
            if keyword in sectionkws:
                if keyword in imapkws and (imaps is None or items[1] in imaps):
                    section = items[1]
                    conf[section] = {}
                else:
                    section = None
            elif keyword == "host" and section:
                conf[section]["server"] = items[1]
            elif keyword == "port" and section:
                conf[section]["port"] = items[1]
            elif keyword == "user" and section:
                conf[section]["user"] = items[1]
            elif keyword == "pass" and section:
                conf[section]["passwd"] = items[1]
            elif keyword == "passcmd" and section:
                passcmd = items[1][1:] if items[1][0] == '+' else items[1]
                pw = subprocess.check_output(passcmd.split())
                pw = pw.decode()
                pw = pw[:-1] if pw[-1] == '\n' else pw
                conf[section]["passwd"] = pw
            elif keyword == "ssltype" and section:
                ssltype2sec = { "none" : "None",
                                "starttls" : "starttls",
                                "imaps" : "explicit-ssl",
                              }
                conf[section]["security"] = ssltype2sec[items[1].lower()]
                if not "port" in conf[section].keys():
                    conf[section]["port"] = 993 if items[1].lower() == "imaps"\
                        else 143
            elif keyword == "certificatefile" and section:
                conf[section]["certfile"] = items[1]
    if type(mboxes) == dict:
        for k in mboxes.keys():
            conf[k]["folders"] = mboxes[k]
    else:
        for k in conf.keys():
            conf[k]["folders"] = ["INBOX"]
    return conf


# Replace .mbsynrc's PassCmd stanzas in IMAPStore sections with 'pass password',
# where password comes from the passwords dict of the form
# {"section-name" : "password" }. Write output to stream 'out'.
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
                if keyword in imapkws and len(items) > 1 \
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
        passwords[k] = conf[k]["passwd"]
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
    print("Triggering sync: {}".format(repr(args)))
    retval = subprocess.call(args)
    print("Sync finished with exit code {}".format(retval))

    confthread.join()
    os.remove(fifoname)
    os.rmdir(os.path.dirname(fifoname))


#if __name__ == '__main__':
#    call_mbsync(pwds)
