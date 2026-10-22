def scheduler(app, settings, request):
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=settings.get("timezone", "UTC"))
    if "cron" in settings:
        trigger = CronTrigger.from_crontab(settings["cron"], timezone=settings.get("timezone", "UTC"))
        options = {}
    else:
        trigger = settings.get("trigger", "interval")
        options = settings.get("trigger_args", {"minutes": 15})

    def run():
        # Each tick has separate adapters and no retained search state.
        fresh = app.fresh()
        result = fresh.run(request or settings["request"])
        if hasattr(result, "close"):
            result.close()

    scheduler.add_job(run, trigger, **options, max_instances=1, coalesce=True)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=True)


ID = "scheduler"
REQUIRES = ()
IMPLEMENTATIONS = {"trigger": scheduler}
