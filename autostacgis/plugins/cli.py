def direct(app, settings, request):
    return app.run(request or settings["request"])


ID = "cli"
REQUIRES = ()
IMPLEMENTATIONS = {"trigger": direct}
