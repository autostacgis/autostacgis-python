from .events import Events


class OpenTelemetry(Events):
    def __init__(self, settings, services):
        super().__init__(settings, services)
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        self.stream = (self.path.parent / "spans.jsonl").open("a")
        self.provider = TracerProvider()
        self.provider.add_span_processor(
            SimpleSpanProcessor(
                ConsoleSpanExporter(out=self.stream, formatter=lambda span: span.to_json(indent=None) + "\n")
            )
        )
        self.tracer = self.provider.get_tracer("autostacgis")

    def emit(self, event, **fields):
        super().emit(event, **fields)
        with self.tracer.start_as_current_span(event) as span:
            for name, value in fields.items():
                if isinstance(value, (str, int, float, bool)):
                    span.set_attribute(name, value)

    def close(self):
        self.provider.shutdown()
        self.stream.close()
        super().close()


ID = "opentelemetry"
REQUIRES = ("events",)
IMPLEMENTATIONS = {"services": OpenTelemetry}
