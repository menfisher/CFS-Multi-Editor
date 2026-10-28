def __getattr__(name: str):
    if name == "app":
        from asgi import app as asgi_app

        return asgi_app
    raise AttributeError(name)
