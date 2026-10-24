"""Ansible: playbooks for everyday jobs, read as YAML and checked for the module, the arguments, the keywords and the handlers
that the job needs. A task that runs a shell command where a module exists does not count.

Where ansible-lint is installed it is run too; the report says whether it was. Nothing here runs a playbook.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question

_YAML = "Rispondi con il solo playbook, in un blocco ```yaml."


def yml(text: str) -> str:
    return f"```yaml\n{text.strip()}\n```"


def _q(n: int, title: str, ask: str, reference: str, wrong: list[str], tasks: list[dict], *, keywords: dict | None = None, handlers: bool = False, names: bool = False,
       must_have: list[str] | None = None, labels: dict[str, str] | None = None, expected: str = "", difficulty: int = 2) -> Question:
    check = {"type": "ansible", "tasks": tasks, "keywords": keywords or {}, "handlers": handlers, "names": names, "must_have": must_have or [], "must_not": [], "labels": labels or {}}
    return make("ansible", n, title, f"{ask}\n{_YAML}", check, expected or title, yml(reference), wrong=[yml(w) for w in wrong], max_tokens=1100, difficulty=difficulty)


def build(seed: int) -> list[Question]:
    out: list[Question] = []

    q1 = """
- name: Install and start nginx
  hosts: web
  become: true
  tasks:
    - name: Install nginx
      ansible.builtin.package:
        name: nginx
        state: present

    - name: Start and enable nginx
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true
"""
    out.append(_q(1, "Installare e avviare nginx", "Scrivi un playbook Ansible che sul gruppo di host `web` installa nginx e lo avvia, abilitandolo anche all'avvio del sistema. Usa i moduli giusti (non `shell` né `command`), "
                  "dai un nome a ogni task e usa i privilegi elevati.", q1,
                  [q1.replace("ansible.builtin.package:\n        name: nginx\n        state: present", "ansible.builtin.shell: apt-get install -y nginx"), q1.replace("  become: true\n", ""), q1.replace("state: started", "state: stopped"),
                   q1.replace("        enabled: true\n", "")],
                  [{"module": ["package", "apt", "dnf", "yum"], "args": {"name": {"eq": "nginx"}, "state": {"in": ["present", "latest"]}}},
                   {"module": ["service", "systemd", "systemd_service"], "args": {"name": {"eq": "nginx"}, "state": {"eq": "started"}, "enabled": {"eq": True}}}],
                  keywords={"become": True}, names=True, difficulty=1))

    q2 = """
- name: Create the deploy user
  hosts: all
  become: true
  tasks:
    - name: Add the deploy user
      ansible.builtin.user:
        name: deploy
        shell: /bin/bash
        groups: sudo
        append: true
        create_home: true
"""
    out.append(_q(2, "Creare un utente", "Scrivi un playbook Ansible che su tutti gli host crea l'utente `deploy` con la shell `/bin/bash`, con la directory home, e lo aggiunge al gruppo `sudo` **senza togliergli gli altri gruppi**. "
                  "Usa i privilegi elevati.", q2, [q2.replace("        append: true\n", ""), q2.replace("ansible.builtin.user:", "ansible.builtin.command: useradd deploy -s /bin/bash -G sudo -m\n      args:").replace("        name: deploy\n        shell: /bin/bash\n        groups: sudo\n        append: true\n        create_home: true\n", ""),
                                                  q2.replace("shell: /bin/bash", "shell: /bin/sh"), q2.replace("  become: true\n", "")],
                  [{"module": ["user"], "args": {"name": {"eq": "deploy"}, "shell": {"eq": "/bin/bash"}, "groups": {"in": ["sudo"]}, "append": {"eq": True}}}], keywords={"become": True}, difficulty=1))

    q3 = """
- name: Configure nginx
  hosts: web
  become: true
  tasks:
    - name: Install the nginx configuration
      ansible.builtin.template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
        mode: "0644"
      notify: Restart nginx

  handlers:
    - name: Restart nginx
      ansible.builtin.service:
        name: nginx
        state: restarted
"""
    out.append(_q(3, "Template e handler", "Scrivi un playbook Ansible per il gruppo `web` che installa il template `nginx.conf.j2` come `/etc/nginx/nginx.conf` con permessi 0644 e, **solo se il file è cambiato**, riavvia il servizio nginx "
                  "tramite un handler chiamato `Restart nginx`.", q3,
                  [q3.replace("      notify: Restart nginx\n", ""), q3.replace("Restart nginx\n      ansible.builtin.service", "Reload nginx\n      ansible.builtin.service"),
                   q3.replace("ansible.builtin.template:\n        src: nginx.conf.j2", "ansible.builtin.copy:\n        src: nginx.conf.j2")],
                  [{"module": ["template"], "args": {"src": {"eq": "nginx.conf.j2"}, "dest": {"eq": "/etc/nginx/nginx.conf"}, "mode": {"in": ["0644", "644", 420]}}}], handlers=True,
                  must_have=["\\bnotify\\s*:\\s*['\"]?Restart nginx", "\\bstate\\s*:\\s*restarted\\b"], labels={"\\bnotify\\s*:\\s*['\"]?Restart nginx": "notify: Restart nginx", "\\bstate\\s*:\\s*restarted\\b": "an handler that restarts the service"}))

    q4 = """
- name: Common tools
  hosts: all
  become: true
  tasks:
    - name: Install the common tools on Debian
      ansible.builtin.apt:
        name: "{{ item }}"
        state: present
        update_cache: true
      loop:
        - git
        - curl
        - htop
      when: ansible_os_family == "Debian"
"""
    out.append(_q(4, "Pacchetti in ciclo", "Scrivi un playbook Ansible che su tutti gli host installa i pacchetti `git`, `curl` e `htop`, ma solo sulle macchine della famiglia Debian (usa il fatto `ansible_os_family`). Usa i privilegi elevati.", q4,
                  [q4.replace('      when: ansible_os_family == "Debian"\n', ""), q4.replace("ansible.builtin.apt", "ansible.builtin.shell").replace('        name: "{{ item }}"\n        state: present\n        update_cache: true\n', "        cmd: apt-get install -y git"),
                   q4.replace("        - htop\n", "")],
                  [{"module": ["apt", "package"], "args": {"state": {"in": ["present", "latest"]}}, "keywords": {"when": True}}], keywords={"become": True},
                  must_have=["\\bgit\\b", "\\bcurl\\b", "\\bhtop\\b", "\\bansible_os_family\\s*==\\s*['\"]Debian['\"]"], labels={"\\bgit\\b": "git", "\\bcurl\\b": "curl", "\\bhtop\\b": "htop", "\\bansible_os_family\\s*==\\s*['\"]Debian['\"]": "the Debian condition"}))

    q5 = """
- name: Harden SSH
  hosts: all
  become: true
  tasks:
    - name: Disable root login
      ansible.builtin.lineinfile:
        path: /etc/ssh/sshd_config
        regexp: '^#?PermitRootLogin'
        line: PermitRootLogin no
      notify: Restart sshd

    - name: Disable password authentication
      ansible.builtin.lineinfile:
        path: /etc/ssh/sshd_config
        regexp: '^#?PasswordAuthentication'
        line: PasswordAuthentication no
      notify: Restart sshd

  handlers:
    - name: Restart sshd
      ansible.builtin.service:
        name: sshd
        state: restarted
"""
    out.append(_q(5, "Rinforzare SSH", "Scrivi un playbook Ansible che su tutti gli host imposta in `/etc/ssh/sshd_config` le righe `PermitRootLogin no` e `PasswordAuthentication no` (sostituendo quelle esistenti, anche se commentate) "
                  "e riavvia il servizio `sshd` con un handler solo se qualcosa è cambiato. Usa i privilegi elevati.", q5,
                  [q5.replace("ansible.builtin.lineinfile", "ansible.builtin.shell", 1).replace("        path: /etc/ssh/sshd_config\n        regexp: '^#?PermitRootLogin'\n        line: PermitRootLogin no\n", "        cmd: echo PermitRootLogin no >> /etc/ssh/sshd_config\n"),
                   q5.replace("PasswordAuthentication no", "PasswordAuthentication yes"), q5.replace("      notify: Restart sshd\n", "")],
                  [{"module": ["lineinfile"], "args": {"path": {"eq": "/etc/ssh/sshd_config"}, "line": {"regex": "PermitRootLogin\\s+no"}}},
                   {"module": ["lineinfile"], "args": {"path": {"eq": "/etc/ssh/sshd_config"}, "line": {"regex": "PasswordAuthentication\\s+no"}}}], keywords={"become": True}, handlers=True,
                  must_have=["\\bstate\\s*:\\s*restarted\\b", "\\bnotify\\b"], labels={"\\bstate\\s*:\\s*restarted\\b": "an handler that restarts the service", "\\bnotify\\b": "notify"}))

    q6 = """
- name: Application directory
  hosts: app
  become: true
  tasks:
    - name: Create the application directory
      ansible.builtin.file:
        path: /opt/app
        state: directory
        owner: appuser
        group: appgroup
        mode: "0750"
"""
    out.append(_q(6, "Directory con permessi", "Scrivi un playbook Ansible che sul gruppo `app` crea la directory `/opt/app` con proprietario `appuser`, gruppo `appgroup` e permessi `0750`. Usa i privilegi elevati.", q6,
                  [q6.replace("state: directory", "state: touch"), q6.replace('mode: "0750"', 'mode: "0777"'), q6.replace("ansible.builtin.file:", "ansible.builtin.command: mkdir -p /opt/app\n      args:").replace("        path: /opt/app\n        state: directory\n        owner: appuser\n        group: appgroup\n        mode: \"0750\"\n", ""),
                   q6.replace("owner: appuser", "owner: root")],
                  [{"module": ["file"], "args": {"path": {"eq": "/opt/app"}, "state": {"eq": "directory"}, "owner": {"eq": "appuser"}, "group": {"eq": "appgroup"}, "mode": {"in": ["0750", "750", 488]}}}], keywords={"become": True}))

    q7 = """
- name: Deploy the release archive
  hosts: app
  become: true
  tasks:
    - name: Download the archive
      ansible.builtin.get_url:
        url: https://example.com/app-1.2.tar.gz
        dest: /tmp/app-1.2.tar.gz
        mode: "0644"

    - name: Unpack it
      ansible.builtin.unarchive:
        src: /tmp/app-1.2.tar.gz
        dest: /opt/app
        remote_src: true
"""
    out.append(_q(7, "Scaricare e scompattare", "Scrivi un playbook Ansible che sul gruppo `app` scarica `https://example.com/app-1.2.tar.gz` in `/tmp/app-1.2.tar.gz` e poi lo scompatta in `/opt/app`: il file è già sulla macchina remota, "
                  "non sul computer di controllo. Usa i privilegi elevati.", q7,
                  [q7.replace("        remote_src: true\n", ""), q7.replace("ansible.builtin.get_url:", "ansible.builtin.command: wget https://example.com/app-1.2.tar.gz\n      args:").replace("        url: https://example.com/app-1.2.tar.gz\n        dest: /tmp/app-1.2.tar.gz\n        mode: \"0644\"\n", ""),
                   q7.replace("dest: /opt/app", "dest: /opt/other")],
                  [{"module": ["get_url"], "args": {"url": {"eq": "https://example.com/app-1.2.tar.gz"}, "dest": {"eq": "/tmp/app-1.2.tar.gz"}}},
                   {"module": ["unarchive"], "args": {"src": {"eq": "/tmp/app-1.2.tar.gz"}, "dest": {"eq": "/opt/app"}, "remote_src": {"eq": True}}}], keywords={"become": True}, difficulty=3))

    q8 = """
- name: Fetch the application
  hosts: app
  become: true
  tasks:
    - name: Clone the repository at a fixed tag
      ansible.builtin.git:
        repo: https://github.com/example/app.git
        dest: /srv/app
        version: v1.4.2
"""
    out.append(_q(8, "Clonare un repository", "Scrivi un playbook Ansible che sul gruppo `app` clona `https://github.com/example/app.git` in `/srv/app` alla versione (tag) `v1.4.2`, con il modulo giusto.", q8,
                  [q8.replace("ansible.builtin.git:", "ansible.builtin.command: git clone https://github.com/example/app.git /srv/app\n      args:").replace("        repo: https://github.com/example/app.git\n        dest: /srv/app\n        version: v1.4.2\n", ""),
                   q8.replace("version: v1.4.2", "version: main"), q8.replace("dest: /srv/app", "dest: /var/app")],
                  [{"module": ["git"], "args": {"repo": {"eq": "https://github.com/example/app.git"}, "dest": {"eq": "/srv/app"}, "version": {"eq": "v1.4.2"}}}], keywords={"become": True}, difficulty=2))

    q9 = """
- name: Nightly backup
  hosts: all
  become: true
  tasks:
    - name: Schedule the backup at 02:30
      ansible.builtin.cron:
        name: nightly backup
        minute: "30"
        hour: "2"
        job: /usr/local/bin/backup.sh
        user: root
"""
    out.append(_q(9, "Backup con cron", "Scrivi un playbook Ansible che su tutti gli host pianifica ogni notte alle 02:30, come utente `root`, l'esecuzione di `/usr/local/bin/backup.sh`, con il modulo `cron` "
                  "(il job si chiama `nightly backup`). Usa i privilegi elevati.", q9,
                  [q9.replace('minute: "30"', 'minute: "0"'), q9.replace('hour: "2"', 'hour: "14"'), q9.replace("ansible.builtin.cron:", "ansible.builtin.shell: echo '30 2 * * * root /usr/local/bin/backup.sh' >> /etc/crontab\n      args:").replace("        name: nightly backup\n        minute: \"30\"\n        hour: \"2\"\n        job: /usr/local/bin/backup.sh\n        user: root\n", "")],
                  [{"module": ["cron"], "args": {"name": {"eq": "nightly backup"}, "minute": {"in": ["30", 30]}, "hour": {"in": ["2", 2]}, "job": {"eq": "/usr/local/bin/backup.sh"}, "user": {"eq": "root"}}}], keywords={"become": True}, difficulty=2))

    q10 = """
- name: Run the health check
  hosts: all
  tasks:
    - name: Run the check script
      ansible.builtin.command: /usr/local/bin/check.sh
      register: check
      changed_when: false
      failed_when: check.rc not in [0, 2]

    - name: Warn when the check is degraded
      ansible.builtin.debug:
        msg: "The service is degraded"
      when: check.rc == 2
"""
    out.append(_q(10, "register, failed_when e when", "Scrivi un playbook Ansible per tutti gli host che esegue `/usr/local/bin/check.sh` e ne registra il risultato; il task non deve fallire se il codice di uscita è 0 o 2 (ma fallire per ogni altro), "
                  "non deve risultare «changed», e un secondo task mostra con `debug` il messaggio «The service is degraded» solo quando il codice di uscita vale 2.", q10,
                  [q10.replace("      failed_when: check.rc not in [0, 2]\n", ""), q10.replace("      when: check.rc == 2\n", ""), q10.replace("      register: check\n", "").replace("check.rc", "rc")],
                  [{"module": ["command", "shell"], "args": {"cmd": {"regex": "check\\.sh"}}, "keywords": {"register": True, "failed_when": True}}, {"module": ["debug"], "keywords": {"when": True}}],
                  must_have=["\\bfailed_when\\b", "\\.rc\\b"], labels={"\\bfailed_when\\b": "failed_when", "\\.rc\\b": "the exit code (.rc)"}, difficulty=3))
    return out
