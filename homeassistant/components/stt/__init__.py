"""Provide functionality to STT."""
from abc import ABC, abstractmethod
import asyncio
import functools as ft
import io
import logging
from typing import Dict, List, Optional

from aiohttp import StreamReader, web
from aiohttp.hdrs import istr
from aiohttp.web_exceptions import HTTPNotFound, HTTPNotImplemented
import attr
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import ATTR_ENTITY_ID, CONF_PLATFORM, ENTITY_MATCH_ALL
from homeassistant.core import callback
from homeassistant.helpers import config_per_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import HomeAssistantType
from homeassistant.setup import async_prepare_setup_platform

from .const import (
    DOMAIN,
    AudioBitrates,
    AudioCodecs,
    AudioFormats,
    AudioSamplerates,
    SpeechResultState,
)

# mypy: allow-untyped-defs, no-check-untyped-defs

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistantType, config):
    """Set up STT."""
    providers = dict()

    async def async_setup_platform(p_type, p_config, disc_info=None):
        """Set up a TTS platform."""
        platform = await async_prepare_setup_platform(hass, config, DOMAIN, p_type)
        if platform is None:
            return

        try:
            provider = await platform.async_get_engine(hass, p_config)
            if provider is None:
                _LOGGER.error("Error setting up platform %s", p_type)
                return

            provider.name = p_type
            provider.hass = hass

            providers[provider.name] = provider
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error setting up platform: %s", p_type)
            return

    setup_tasks = [
        async_setup_platform(p_type, p_config)
        for p_type, p_config in config_per_platform(config, DOMAIN)
    ]

    if setup_tasks:
        await asyncio.wait(setup_tasks)

    hass.http.register_view(SpeechToTextView(providers))
    return True


@attr.s
class SpeechMetadata:
    """Metadata of audio stream."""

    lanugage: str = attr.ib()
    format: AudioFormats = attr.ib()
    codec: AudioCodecs = attr.ib()
    bitrate: AudioBitrates = attr.ib(converter=int)
    samplerate: AudioSamplerates = attr.ib(converter=int)


@attr.s
class SpeechResult:
    """Result of audio Speech."""

    text: str = attr.ib()
    state: SpeechResultState = attr.ib()


class Provider(ABC):
    """Represent a single STT provider."""

    hass: Optional[HomeAssistantType] = None
    name: Optional[str] = None

    @property
    @abstractmethod
    def supported_languages(self) -> List[str]:
        """Return a list of supported languages."""

    @property
    @abstractmethod
    def supported_formats(self) -> List[AudioFormats]:
        """Return a list of supported formats."""

    @property
    @abstractmethod
    def supported_codecs(self) -> List[AudioCodecs]:
        """Return a list of supported codecs."""

    @property
    @abstractmethod
    def supported_bitrates(self) -> List[AudioBitrates]:
        """Return a list of supported bitrates."""

    @property
    @abstractmethod
    def supported_samplerates(self) -> List[AudioSamplerates]:
        """Return a list of supported samplerates."""

    @abstractmethod
    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: StreamReader
    ) -> SpeechResult:
        """Process an audio stream to STT service.

        Only streaming of content are allow!
        """

    @callback
    def check_metadata(self, metadata: SpeechMetadata) -> bool:
        """Check if given metadata supported by this provider."""
        if (
            metadata.lanugage not in self.supported_languages
            or metadata.format not in self.supported_formats
            or metadata.codec not in self.supported_codecs
            or metadata.bitrate not in self.supported_bitrates
            or metadata.samplerate not in self.supported_samplerates
        ):
            return False
        return True


class SpeechToTextView(HomeAssistantView):
    """STT view to generate a text from audio stream."""

    requires_auth = True
    url = "/api/stt/{provider}"
    name = "api:stt:provider"

    def __init__(self, providers: Dict[str, Provider]) -> None:
        """Initialize a tts view."""
        self.providers = providers

    def _metadat_from_header(self, request: web.Request) -> SpeechMetadata:
        """Extract metadata from header.

        X-Speech-Content: format=wav; codecs=pcm; samplerate=16000; bitrate=16; language=de_de
        """
        data = request.headers[istr("X-Speech-Content")].split(";")
        map(str.strip, data)

        # Convert Header data
        args = dict()
        for value in data:
            args[value.partition("=")[0]] = value.partition("=")[2]

        return SpeechMetadata(**args)

    async def post(self, request: web.Request, provider: str) -> web.Response:
        """Convert Speech (audio) to text."""
        if provider not in self.providers:
            raise HTTPNotFound()
        stt_provider: Provider = self.providers[provider]

        # Check format
        metadata = self._metadat_from_header(request)
        if not stt_provider.check_metadata(metadata):
            raise HTTPNotImplemented()

        # Process audio stream
        result = await stt_provider.async_process_audio_stream(
            metadata, request.content
        )

        # Return result
        return self.json_message(attr.asdict(result))

    async def get(self, request: web.Request, provider: str) -> web.Response:
        """Return provider specific audio information."""
        if provider not in self.providers:
            raise HTTPNotFound()
        stt_provider: Provider = self.providers[provider]

        return self.json_message(
            {
                "languages": stt_provider.supported_languages,
                "formats": stt_provider.supported_formats,
                "codecs": stt_provider.supported_codecs,
                "samplerates": stt_provider.supported_samplerates,
                "bitrates": stt_provider.supported_bitrates,
            }
        )
