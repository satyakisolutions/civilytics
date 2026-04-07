# pipeline/app/plugin_loader.py
"""GStreamer plugin loader for WasteWatch custom plugins.

Handles discovery and registration of the wastewatchmeta plugin,
with fallback to Python-based processing if the C plugin is not available.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)

# Plugin factory name registered by the C plugin's GST_PLUGIN_DEFINE.
_PLUGIN_FACTORY_NAME = "wastewatchmeta"

# Well-known paths where the shared library might be installed.
_SEARCH_PATHS: list[str] = [
    "/opt/nvidia/deepstream/deepstream/lib/gst-plugins/",
    "./plugins/gstwastewatchmeta/",
    "/usr/lib/x86_64-linux-gnu/gstreamer-1.0/",
    "/usr/lib/aarch64-linux-gnu/gstreamer-1.0/",
]

# Possible library filenames (standard GStreamer naming conventions).
_LIB_NAMES: list[str] = [
    "libgstwastewatchmeta.so",
    "gstwastewatchmeta.so",
]


def _find_plugin_library() -> str | None:
    """Search well-known paths for the wastewatchmeta shared object.

    Also honours the ``GST_WASTEWATCH_PLUGIN_PATH`` environment variable
    which, when set, is checked first.

    Returns:
        Absolute path to the ``.so`` file, or ``None`` if not found.
    """
    # Allow explicit override via environment variable.
    env_path = os.environ.get("GST_WASTEWATCH_PLUGIN_PATH")
    if env_path:
        expanded = os.path.expanduser(env_path)
        if os.path.isfile(expanded):
            return expanded
        # If it is a directory, search inside it.
        if os.path.isdir(expanded):
            for lib_name in _LIB_NAMES:
                candidate = os.path.join(expanded, lib_name)
                if os.path.isfile(candidate):
                    return candidate

    for search_dir in _SEARCH_PATHS:
        for lib_name in _LIB_NAMES:
            candidate = os.path.join(search_dir, lib_name)
            if os.path.isfile(candidate):
                return candidate

    return None


def ensure_plugin_loaded() -> bool:
    """Ensure the ``wastewatchmeta`` GStreamer plugin is available.

    First checks whether the plugin factory is already in the GStreamer
    registry (e.g. because ``GST_PLUGIN_PATH`` includes the right
    directory).  If not, attempts to locate the shared library on disk
    and load it manually via ``Gst.Registry.get().add_plugin``.

    Returns:
        ``True`` if the plugin is available (either already registered or
        successfully loaded), ``False`` otherwise.
    """
    registry = Gst.Registry.get()

    # Fast path: already registered.
    factory = Gst.ElementFactory.find(_PLUGIN_FACTORY_NAME)
    if factory is not None:
        logger.info(
            "wastewatchmeta plugin already registered (version %s)",
            factory.get_metadata("version") or "unknown",
        )
        return True

    # Try to find and load the shared library.
    lib_path = _find_plugin_library()
    if lib_path is None:
        logger.warning(
            "wastewatchmeta shared library not found in search paths: %s",
            _SEARCH_PATHS,
        )
        return False

    logger.info("Loading wastewatchmeta plugin from %s", lib_path)

    # Ensure the parent directory is in GST_PLUGIN_PATH so GStreamer can
    # resolve the plugin.  Then re-scan.
    plugin_dir = str(Path(lib_path).parent)
    existing_path = os.environ.get("GST_PLUGIN_PATH", "")
    if plugin_dir not in existing_path.split(":"):
        new_path = f"{plugin_dir}:{existing_path}" if existing_path else plugin_dir
        os.environ["GST_PLUGIN_PATH"] = new_path
        logger.debug("Prepended %s to GST_PLUGIN_PATH", plugin_dir)

    # Ask GStreamer to scan the directory for new plugins.
    scan_result = registry.scan_path(plugin_dir)
    if not scan_result:
        logger.warning(
            "Gst.Registry.scan_path(%s) reported no new plugins", plugin_dir
        )

    # Verify registration succeeded.
    factory = Gst.ElementFactory.find(_PLUGIN_FACTORY_NAME)
    if factory is not None:
        logger.info("wastewatchmeta plugin loaded successfully from %s", lib_path)
        return True

    logger.error(
        "Failed to register wastewatchmeta plugin after loading %s", lib_path
    )
    return False


def create_wastewatch_element(name: str = "ww_meta") -> Gst.Element | None:
    """Create a ``wastewatchmeta`` GStreamer element.

    Calls ``ensure_plugin_loaded`` first so the caller does not need to
    manage plugin discovery separately.

    Args:
        name: Element instance name in the pipeline (default ``ww_meta``).

    Returns:
        A configured ``Gst.Element``, or ``None`` if the plugin is not
        available.
    """
    if not ensure_plugin_loaded():
        logger.warning(
            "Cannot create wastewatchmeta element — plugin not available. "
            "Falling back to Python-based metadata processing."
        )
        return None

    element = Gst.ElementFactory.make(_PLUGIN_FACTORY_NAME, name)
    if element is None:
        logger.error(
            "Gst.ElementFactory.make('%s', '%s') returned None",
            _PLUGIN_FACTORY_NAME,
            name,
        )
        return None

    logger.info("Created wastewatchmeta element '%s'", name)
    return element


def configure_element(
    element: Gst.Element,
    enter_threshold_s: float,
    observe_threshold_s: float,
    exit_timeout_s: float,
    roi_config_path: str,
    input_width: int,
    input_height: int,
) -> None:
    """Set GObject properties on a ``wastewatchmeta`` element.

    Property names must match those registered in the C plugin via
    ``g_object_class_install_property``.  If a property does not exist
    on the element (e.g. running an older plugin version), the error is
    logged but does not raise.

    Args:
        element: The wastewatchmeta GStreamer element.
        enter_threshold_s: Seconds before IDLE -> ENTER transition.
        observe_threshold_s: Seconds in-ROI before OBSERVE -> VIOLATION.
        exit_timeout_s: Maximum seconds before forced EXITED transition.
        roi_config_path: Filesystem path to the ROI polygon config file.
        input_width: Input frame width in pixels (for coordinate scaling).
        input_height: Input frame height in pixels (for coordinate scaling).
    """
    props = {
        "enter-threshold-s": float(enter_threshold_s),
        "observe-threshold-s": float(observe_threshold_s),
        "exit-timeout-s": float(exit_timeout_s),
        "roi-config-path": str(roi_config_path),
        "input-width": int(input_width),
        "input-height": int(input_height),
    }

    for prop_name, prop_value in props.items():
        try:
            element.set_property(prop_name, prop_value)
            logger.debug(
                "Set %s.%s = %r", element.get_name(), prop_name, prop_value
            )
        except TypeError as exc:
            logger.warning(
                "Property '%s' not found on element '%s': %s",
                prop_name,
                element.get_name(),
                exc,
            )

    logger.info(
        "Configured wastewatchmeta '%s': enter=%.1fs observe=%.1fs "
        "exit=%.1fs roi=%s resolution=%dx%d",
        element.get_name(),
        enter_threshold_s,
        observe_threshold_s,
        exit_timeout_s,
        roi_config_path,
        input_width,
        input_height,
    )
