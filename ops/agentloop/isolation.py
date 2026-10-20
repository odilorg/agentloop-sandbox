#!/usr/bin/env python3
"""Negative isolation tests, run INSIDE agentloop@isolation.service as the agentloop user.

Proves what the runner (and the Claude process it starts) cannot reach. It never reads a
protected file: it only checks whether a path can be opened, listed or connected to.
"""
import errno, grp, json, os, pwd, re, socket

PROTECTED = [
    "/home", "/home/odil", "/home/odil/.ssh", "/home/odil/.claude", "/home/odil/.config",
    "/home/odil/.config/agentloop", "/home/odil/projects/id_rsa", "/root", "/run/user/1000",
    "/data", "/data/projects", "/var/www", "/srv",
    "/etc/agentloop", "/etc/agentloop/app.pem", "/etc/agentloop/claude-token", "/etc/shadow", "/etc/sudoers",
    "/run/docker.sock", "/var/run/docker.sock", "/var/lib/docker",
    "/run/mysqld", "/var/lib/mysql", "/var/lib/fwupd", "/boot", "/run/postgresql", "/var/lib/postgresql", "/var/lib/redis",
]
BLOCKED_NET = [("127.0.0.1", 3306), ("127.0.0.1", 5432), ("127.0.0.1", 6379), ("127.0.0.1", 22), ("127.0.0.1", 80),
               ("::1", 3306), ("192.168.0.1", 80), ("192.168.0.122", 22), ("100.112.205.8", 22), ("172.17.0.1", 80),
               ("10.0.0.1", 80)]
ALLOWED_NET = [("api.github.com", 443), ("github.com", 443), ("api.anthropic.com", 443)]
SENSITIVE = re.compile(r"(^\.env|\.env$|\.pem$|\.key$|^id_(rsa|ed25519|ecdsa)|\.p12$|\.pfx$|^\.my\.cnf$|^\.pgpass$|"
                       r"^\.netrc$|credential|secret|password|token(?!izer)|^wp-config\.php$|^auth\.json$|\.sqlite3?$|\.db$)", re.I)
PUBLIC_PREFIXES = ("/usr/share/", "/usr/lib/", "/usr/lib64/", "/usr/libexec/", "/usr/include/", "/usr/src/",
                   "/etc/ssl/certs/", "/etc/ca-certificates/", "/usr/local/share/ca-certificates/", "/snap/",
                   "/usr/bin/", "/usr/sbin/", "/opt/agentloop/", "/var/lib/agentloop/", "/etc/pki/", "/var/cache/",
                   "/etc/pam.d/", "/etc/apparmor.d/", "/etc/xdg/autostart/", "/etc/apt/keyrings/")
SKIP_DIRS = ("/proc", "/sys", "/dev", "/run/credentials")


def blocked(path):
    """True if the path cannot be opened for reading or listed."""
    try:
        if os.path.isdir(path):
            os.listdir(path)
            return False
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)     # open only; nothing is read
        os.close(fd)
        return False
    except OSError as e:
        return e.errno in (errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENXIO, errno.ENOTDIR)


def connect(host, port):
    try:
        s = socket.create_connection((host, port), timeout=4)
        s.close()
        return "connected"
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e)) if e.errno else type(e).__name__


def main(c):
    results = []

    def rec(name, ok, detail=""):
        results.append({"test": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + (f" :: {detail}" if detail else ""), flush=True)

    me = pwd.getpwuid(os.getuid()).pw_name
    groups = sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
    rec("runs as the dedicated agentloop user", me == "agentloop" and os.getuid() != 0, f"user={me} groups={groups}")
    rec("no admin groups", not set(groups) & {"sudo", "adm", "docker", "root", "lxd", "wheel", "odil"}, str(groups))
    status = open("/proc/self/status").read()
    caps = re.search(r"^CapEff:\s*(\S+)", status, re.M).group(1)
    nnp = re.search(r"^NoNewPrivs:\s*(\S+)", status, re.M).group(1)
    rec("no capabilities, no privilege escalation", int(caps, 16) == 0 and nnp == "1", f"CapEff={caps} NoNewPrivs={nnp}")

    for p in PROTECTED:
        rec(f"cannot open {p}", blocked(p))
    for p in ("/etc/agentloop-write-test", "/usr/local/agentloop-write-test", "/var/lib/agentloop-inbox/write-test",
              "/opt/agentloop/write-test", "/home/agentloop-write-test"):
        try:
            with open(p, "w"):
                pass
            os.unlink(p)
            rec(f"cannot write {p}", False, "write succeeded")
        except OSError as e:
            rec(f"cannot write {p}", True, errno.errorcode.get(e.errno, ""))
    try:
        socket.socket(socket.AF_UNIX).close()
        rec("cannot create Unix sockets (Docker, MySQL, D-Bus)", False, "AF_UNIX allowed")
    except OSError as e:
        rec("cannot create Unix sockets (Docker, MySQL, D-Bus)", True, errno.errorcode.get(e.errno, ""))

    for host, port in BLOCKED_NET:
        r = connect(host, port)
        # The IP filter drops packets: a blocked connect times out (or EPERM). "connected" or a refusal
        # means packets reached the target, so both count as FAIL.
        rec(f"network blocked to {host}:{port}", r in ("EPERM", "TimeoutError", "timeout", "ETIMEDOUT"), r)
    for host, port in ALLOWED_NET:
        r = connect(host, port)
        rec(f"network allowed to {host}:{port}", r == "connected", r)

    others = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if os.stat(f"/proc/{pid}").st_uid != os.getuid():
                others.append(pid)
        except OSError:
            pass
    rec("other users' processes are invisible", not others, f"{len(others)} visible")

    found, scanned = [], 0
    for root, dirs, files in os.walk("/", onerror=lambda e: None):
        if root.startswith(SKIP_DIRS):
            dirs[:] = []
            continue
        for f in files:
            scanned += 1
            p = os.path.join(root, f)
            if SENSITIVE.search(f) and not p.startswith(PUBLIC_PREFIXES) and os.access(p, os.R_OK) \
                    and not os.path.islink(p):
                found.append(p)
    rec("no readable credential-like files outside public system paths", not found,
        f"scanned {scanned} files; readable: {found[:40]}")

    passed = sum(r["result"] == "PASS" for r in results)
    print(f"\n{passed}/{len(results)} PASS", flush=True)
    try:
        (c["log_dir"] / "isolation.json").write_text(json.dumps(results, indent=1))
    except OSError:
        pass
    return 0 if passed == len(results) else 1
