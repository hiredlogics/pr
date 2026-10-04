from ._env import load_env, admin_headers
flags = load_env()
print("env_flags", flags)
print("admin_headers_set", bool(admin_headers()))
