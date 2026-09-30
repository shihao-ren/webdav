"""账号 -> 挂载点 / realm 映射（纯逻辑，便于单元测试）。

WsgiDAV 的 SimpleDomainController 以挂载路径（share_path）作为 realm 名：
请求落到哪个 provider，就要求认证该 provider 的 realm，而 realm 只含我们
配置的账号。据此，把"每个服务的账号"限制到"它自己的前缀挂载点"，天然实现
服务间隔离——服务账号无法认证到其它服务的 realm，除非该 realm 也配了它。
"""


def mount_realm_of(prefix):
    """前缀 -> 挂载路径（默认直接作为 realm）。

    ''(桶根) -> '/'; 'kg-viewer-backups' -> '/kg-viewer-backups'。
    """
    p = (prefix or "").strip("/")
    return "/" + p if p else "/"


def build_user_mapping(accounts):
    """构造 SimpleDomainController 的 user_mapping。

    accounts: {user: {'password':…, 'prefix':…}}。

    返回 {realm: {user: {'password':…}}}：每个前缀一个 realm，只放该前缀的
    账号；桶根账号（prefix 为空）是 admin，追加到所有 realm 以保持全量访问。

    >>> u = build_user_mapping({
    ...     "admin": {"password": "a", "prefix": ""},
    ...     "kgviewer": {"password": "k", "prefix": "kg-viewer-backups"},
    ...     "vault": {"password": "v", "prefix": "vault-backups"},
    ... })
    >>> u["/kg-viewer-backups"] == {"kgviewer": {"password": "k"},
    ...                              "admin": {"password": "a"}}
    True
    >>> u["/vault-backups"] == {"vault": {"password": "v"},
    ...                          "admin": {"password": "a"}}
    True
    """
    user_mapping = {}
    admin_entries = {}
    for user, conf in accounts.items():
        realm = mount_realm_of(conf["prefix"])
        entry = {"password": conf["password"]}
        user_mapping.setdefault(realm, {})[user] = entry
        if conf["prefix"].strip("/") == "":
            admin_entries[user] = entry
    # admin（桶根）全量：追加到所有服务 realm
    for realm, users in user_mapping.items():
        if realm == "/":
            continue
        for user, entry in admin_entries.items():
            users.setdefault(user, entry)
    return user_mapping