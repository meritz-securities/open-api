"""Standalone HTTP-only AI wiring. No CLI/MCP registration or implicit runs."""
from .ai_http_provider import HttpProviderConfig, generate_candidates
from .ai_session import AISession
from .ai_generation_binding import safe_http_improvement

# Server policy, not an assertion of account access or model availability.
MODELS = ('claude-opus-5-5',)
HTTP_ACTIONS = frozenset({'get_ai_connection', 'connect_ai_session', 'disconnect_ai_session',
                          'create_ai_draft', 'get_ai_draft', 'generate_ai_draft', 'cancel_ai_draft',
                          'preview_ai_http_improvement', 'generate_ai_http_improvement'})


class AIStudio:
    def __init__(self, store, *, management_allowed=False, service=None):
        self.store = store
        self._service = service
        self.session = AISession(config_factory=HttpProviderConfig, send=generate_candidates,
                                 models=MODELS, management_allowed=management_allowed)

    def _controller(self, lease=None):
        from .ai_drafts import DraftController
        return DraftController(self.store, provider=lease.generate if lease else None,
                               metadata=lease.metadata if lease else None)

    def get_ai_connection(self):
        return self.session.status()

    def connect_ai_session(self, *, apiKey, model, workspaceId=None,
                           maxOutputTokens=4096, timeoutSeconds=120):
        return self.session.connect(apiKey=apiKey, model=model, workspaceId=workspaceId,
                                    maxOutputTokens=maxOutputTokens, timeoutSeconds=timeoutSeconds)

    def disconnect_ai_session(self):
        return self.session.disconnect()

    def create_ai_draft(self, *, request, connectionId, requestId=None):
        # Pin the exact preview configuration; lease acquisition does not send.
        with self.session.lease(connectionId) as lease:
            return self._controller(lease).create(request, requestId=requestId)

    def get_ai_draft(self, *, requestId):
        return self._controller().get(requestId)

    def cancel_ai_draft(self, *, requestId):
        return self._controller().cancel(requestId)

    def generate_ai_draft(self, *, requestId, promptHash, consentVersion):
        saved = self.get_ai_draft(requestId=requestId)
        # A retry is a read once claimed or terminal. It never re-invokes the
        # provider and works after disconnect/restart, without acquiring a key.
        if saved['state'] != 'preview':
            return saved
        with self.session.lease(saved['metadata']['connectionId']) as lease:
            return self._controller(lease).generate(requestId, promptHash, consentVersion)

    @safe_http_improvement
    def preview_ai_http_improvement(self, *, requestId, connectionId=None):
        from . import ai_generation, ai_requests
        from .ai_generation_binding import GenerationBinding
        ai_generation._platform()
        saved = ai_generation.saved_http_preview(self._service, requestId)
        if saved is not None:
            # A stored preview describes the exact original consent, not current settings.
            return saved
        with self.session.lease(connectionId) as lease:
            return ai_generation.preview_http(self._service, requestId,
                                               GenerationBinding(lease.metadata, lease.generate))

    @safe_http_improvement
    def generate_ai_http_improvement(self, *, requestId, promptHash, consentVersion):
        from . import ai_generation, ai_requests
        from .ai_generation_binding import GenerationBinding
        saved = ai_generation.saved_http_preview(self._service, requestId)
        if saved is None:
            raise ValueError('AI_GENERATION_PREVIEW_REQUIRED')
        prior = ai_requests.optional(self.store, 'ai-generation', requestId)
        if prior is not None or ai_requests.optional(self.store, 'ai-request-job', 'ai-'+requestId) is not None:
            return ai_generation.generate_http(self._service, requestId, promptHash, consentVersion)
        with self.session.lease(saved['connectionId']) as lease:
            return ai_generation.generate_http(self._service, requestId, promptHash, consentVersion,
                                                GenerationBinding(lease.metadata, lease.generate))

    def invoke(self, action, arguments):
        if action not in HTTP_ACTIONS:
            raise ValueError('AI_STUDIO_ACTION_UNSUPPORTED')
        return getattr(self, action)(**arguments)
