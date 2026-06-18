from __future__ import annotations

import datetime
import importlib
import inspect
import json
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

PREFIX = "FNCDBG"
LOG_SEQ = 0
_LOGGING_FILE_EVENT = False
_IN_DEBUG_WRAPPER = threading.local()
_ORIGINAL_OPTS_SET = None
_ORIGINAL_OPTS_SAVE = None
_LAST_SNAPSHOT = None
_POLL_THREAD_STARTED = False

SCRIPT_PATH = Path(__file__).resolve()
try:
    EXTENSION_ROOT = Path(__file__).resolve().parents[1]
except Exception:
    EXTENSION_ROOT = Path(os.getcwd()).resolve()
LOG_DIR = EXTENSION_ROOT / "logs"
STARTED = datetime.datetime.now()
STAMP = STARTED.strftime("%Y%m%d_%H%M%S")
LOG_FILE_TXT = LOG_DIR / f"forge_neo_compat_debugger_{STAMP}.txt"
LOG_FILE_JSONL = LOG_DIR / f"forge_neo_compat_debugger_{STAMP}.jsonl"

EXPECTED_CHOICES = ["None", "Diffusers", "ComfyUI", "WebUI 1.5", "InvokeAI", "EasyDiffusion", "DrawThings"]
WATCHED_KEYS = [
    "forge_try_reproduce", "neo_forge_try_reproduce",
    "forge_neo_compat_enabled", "forge_neo_compat_debug_logging", "forge_neo_compat_force_patch_reinstall",
    "randn_source", "emphasis", "auto_backcompat", "use_old_emphasis_implementation",
    "use_old_karras_scheduler_sigmas", "no_dpmpp_sde_batch_determinism",
    "use_old_hires_fix_width_height", "hires_fix_use_firstpass_conds", "use_old_scheduling",
    "use_downcasted_alpha_bar", "refiner_switch_by_sample_steps", "sdxl_crop_top", "sdxl_crop_left",
]
FUZZY_SUBSTRINGS = ["compat", "reproduce", "rng", "randn", "karras", "sigma", "hires", "scheduling", "alpha", "refiner", "emphasis", "crop"]
EXPECTED_PRESETS = {
    "None": {"runtime_force_noise_source": None, "options": {}},
    "ComfyUI": {"runtime_force_noise_source": "CPU", "options": {"randn_source": "CPU", "emphasis": "Original", "sdxl_crop_top": 0, "sdxl_crop_left": 0}},
    "Diffusers": {"runtime_force_noise_source": "CPU", "options": {"randn_source": "CPU", "emphasis": "Original", "sdxl_crop_top": 0, "sdxl_crop_left": 0}},
    "InvokeAI": {"runtime_force_noise_source": "CPU", "options": {"randn_source": "CPU", "emphasis": "Original", "sdxl_crop_top": 0, "sdxl_crop_left": 0}},
    "DrawThings": {"runtime_force_noise_source": "CPU", "options": {"randn_source": "CPU", "emphasis": "Original"}},
    "EasyDiffusion": {"runtime_force_noise_source": "CPU", "options": {"randn_source": "CPU", "emphasis": "Original"}},
    "WebUI 1.5": {"runtime_force_noise_source": None, "options": {"emphasis": "Original", "use_old_hires_fix_width_height": True, "hires_fix_use_firstpass_conds": True, "use_old_scheduling": True, "use_downcasted_alpha_bar": True}},
}


def safe_json(payload):
    try:
        return json.dumps(payload or {}, sort_keys=True, default=str, separators=(",", ":"))
    except Exception as exc:
        return json.dumps({"json_error": str(exc), "payload_repr": repr(payload)}, default=str)


def _setting(name, default):
    try:
        from modules import shared
        opts = getattr(shared, "opts", None)
        if opts is None:
            return default
        return getattr(opts, name, getattr(getattr(opts, "data", {}), "get", lambda *_: default)(name, default))
    except Exception:
        return default


def append_txt_log(phase, message, payload, timestamp, seq):
    global _LOGGING_FILE_EVENT
    if _setting("forge_neo_compat_debugger_log_txt", True) is False:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE_TXT.open("a", encoding="utf8") as f:
            f.write(f"[FNCDBG][{seq:06d}][{phase}][{timestamp}] {message}\n")
            if payload:
                f.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
            f.write("\n")
        if not _LOGGING_FILE_EVENT and phase != "file.log":
            _LOGGING_FILE_EVENT = True
            print(f"[FNCDBG][file.log] wrote text log event", flush=True)
            _LOGGING_FILE_EVENT = False
    except Exception as exc:
        print(f"[FNCDBG][file.log] failed to write text log event: {exc}", flush=True)


def log_event(phase, message, payload=None):
    global LOG_SEQ
    LOG_SEQ += 1
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    payload = payload or {}
    print(f"[FNCDBG][{LOG_SEQ:06d}][{phase}][{ts}] {message} | {safe_json(payload)}", flush=True)
    append_txt_log(phase, message, payload, ts, LOG_SEQ)
    if _setting("forge_neo_compat_debugger_log_jsonl", False):
        try:
            with LOG_FILE_JSONL.open("a", encoding="utf8") as f:
                f.write(json.dumps({"seq": LOG_SEQ, "phase": phase, "timestamp": ts, "message": message, "payload": payload}, default=str) + "\n")
        except Exception as exc:
            print(f"[FNCDBG][file.log] failed to write jsonl log event: {exc}", flush=True)


def log_exception(phase, exc):
    log_event("error", f"exception in {phase}: {exc}", {"traceback": traceback.format_exc()})

try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE_TXT.open("a", encoding="utf8") as f:
        f.write("Forge Neo Compat Debugger log\n")
        f.write(f"Started: {STARTED.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Extension root: {EXTENSION_ROOT}\n")
        f.write(f"Log file: {LOG_FILE_TXT}\n\n")
    log_event("file.log", "created text log file", {"path": str(LOG_FILE_TXT)})
except Exception as exc:
    print(f"[FNCDBG][file.log] failed to write text log event: {exc}", flush=True)


def try_import(name, attr=None):
    try:
        mod = importlib.import_module(name)
        obj = getattr(mod, attr) if attr else mod
        return True, obj, None
    except Exception as exc:
        return False, None, traceback.format_exc()

imports = {}
for name, attr in [("modules.shared", None), ("modules.script_callbacks", None), ("modules.options", "OptionInfo"), ("modules.scripts", "Script"), ("gradio", None), ("forge_neo_compat", None), ("forge_neo_compat.presets", None), ("forge_neo_compat.options", None), ("forge_neo_compat.patch_manager", None)]:
    ok, obj, tb = try_import(name, attr)
    imports[f"{name}{'.'+attr if attr else ''}"] = {"ok": ok, "object": str(obj) if ok else None, "traceback": tb if not ok else None}
log_event("browser.ui", "Browser script diagnostics are emitted in the browser console; Browser click observed but no matching shared.opts change was detected if console click has no corresponding Python opts.diff/opts.set event", {"browser_prefix": "[FNCDBG-BROWSER]"})
log_event("import", "startup diagnostics", {"extension_root": str(EXTENSION_ROOT), "cwd": os.getcwd(), "text_log_file": str(LOG_FILE_TXT), "python": sys.version, "platform": platform.platform(), "imports": imports})


def enabled(): return bool(_setting("forge_neo_compat_debugger_enabled", True))
def verbose(): return bool(_setting("forge_neo_compat_debugger_verbose", True))
def trace_option_set(): return bool(_setting("forge_neo_compat_debugger_trace_option_set", True))
def trace_config_save(): return bool(_setting("forge_neo_compat_debugger_trace_config_save", True))
def trace_sampling(): return bool(_setting("forge_neo_compat_debugger_trace_sampling", True))
def trace_components(): return bool(_setting("forge_neo_compat_debugger_trace_components", True))
def watch_interval():
    try: return max(50, int(_setting("forge_neo_compat_debugger_watch_interval_ms", 500))) / 1000.0
    except Exception: return 0.5


def get_shared():
    from modules import shared
    return shared


def opts_data():
    try: return getattr(get_shared().opts, "data", {}) or {}
    except Exception: return {}


def opts_labels():
    try: return getattr(get_shared().opts, "data_labels", {}) or {}
    except Exception: return {}


def get_opt_value(key):
    try:
        opts = get_shared().opts
        if hasattr(opts, "data") and key in opts.data:
            return opts.data.get(key)
        return getattr(opts, key)
    except Exception:
        return "<missing>"


def snapshot_opts(include_discovered=True):
    keys = list(WATCHED_KEYS)
    if include_discovered:
        for key in discover_keys():
            if key not in keys: keys.append(key)
    return {key: get_opt_value(key) for key in keys}


def discover_keys():
    keys = set(opts_data().keys()) | set(opts_labels().keys())
    return sorted(k for k in keys if any(s in str(k).lower() for s in FUZZY_SUBSTRINGS))


def diff_snapshots(old, new):
    changes = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key, "<missing>") != new.get(key, "<missing>"):
            changes[key] = {"old": old.get(key, "<missing>"), "new": new.get(key, "<missing>")}
    return changes


def selected_preset_from_snapshot(snap):
    for key in ("forge_try_reproduce", "neo_forge_try_reproduce"):
        val = snap.get(key, "<missing>")
        if val not in ("<missing>", None, ""):
            return val
    return None


def log_diff(old, new, reason):
    changes = diff_snapshots(old, new)
    log_event("opts.diff", "watched option diff", {"reason": reason, "changes": changes})
    maybe_compare_expected_preset(old, new)


def maybe_compare_expected_preset(old, new):
    old_sel, new_sel = selected_preset_from_snapshot(old), selected_preset_from_snapshot(new)
    if old_sel == new_sel:
        return
    expected = EXPECTED_PRESETS.get(new_sel)
    log_event("preset.expected", f"Selected preset changed {old_sel} -> {new_sel}", {"valid": new_sel in EXPECTED_PRESETS, "expected": expected})
    time.sleep(watch_interval())
    post = snapshot_opts()
    log_event("preset.actual", "Actual post-change state", post)
    if expected:
        missing = []
        extra = {}
        for key, val in expected["options"].items():
            actual = post.get(key, "<missing>")
            if actual == "<missing>": missing.append(key)
            elif actual != val: log_event("preset.actual", "Expected change missing", {"key": key, "expected": val, "actual": actual})
        for key, change in diff_snapshots(old, post).items():
            if key not in expected["options"] and key not in ("forge_try_reproduce", "neo_forge_try_reproduce"):
                extra[key] = change
        if missing: log_event("preset.actual", "Missing keys prevented preset application", {"missing_keys": missing})
        if extra: log_event("preset.actual", "Actual changes not predicted by expected preset table", extra)


def summarize_stack():
    frames = []
    for fr in inspect.stack()[2:10]:
        frames.append(f"{Path(fr.filename).name}:{fr.function}:{fr.lineno}")
    return " <- ".join(frames)


def read_json_file(path):
    try:
        if path and Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf8"))
    except Exception as exc:
        return {"<read_error>": str(exc)}
    return {}


def patch_opts():
    global _ORIGINAL_OPTS_SET, _ORIGINAL_OPTS_SAVE
    if not enabled(): return
    try:
        opts = get_shared().opts
        if trace_option_set() and hasattr(opts, "set") and not getattr(opts.set, "_fncdbg_wrapped", False):
            _ORIGINAL_OPTS_SET = opts.set
            def debug_opts_set(key, value, *args, **kwargs):
                if getattr(_IN_DEBUG_WRAPPER, "active", False):
                    return _ORIGINAL_OPTS_SET(key, value, *args, **kwargs)
                old = snapshot_opts(); run_callbacks = kwargs.get("run_callbacks", "<default>")
                log_event("opts.set", "shared.opts.set called", {"key": key, "old": old.get(key, "<missing>"), "new": value, "run_callbacks": run_callbacks, "caller": summarize_stack()})
                try:
                    _IN_DEBUG_WRAPPER.active = True
                    result = _ORIGINAL_OPTS_SET(key, value, *args, **kwargs)
                except Exception as exc:
                    log_exception("opts.set", exc); raise
                finally:
                    _IN_DEBUG_WRAPPER.active = False
                log_diff(old, snapshot_opts(), f"after opts.set({key})")
                return result
            debug_opts_set._fncdbg_wrapped = True
            opts.set = debug_opts_set
            log_event("opts.set", "wrapped shared.opts.set", {})
        if trace_config_save() and hasattr(opts, "save") and not getattr(opts.save, "_fncdbg_wrapped", False):
            _ORIGINAL_OPTS_SAVE = opts.save
            def debug_opts_save(filename=None, *args, **kwargs):
                old_snap = snapshot_opts(); cfg = filename or getattr(get_shared(), "config_filename", None)
                before_cfg = read_json_file(cfg)
                log_event("opts.save", "shared.opts.save called", {"filename": cfg, "snapshot": old_snap, "caller": summarize_stack()})
                try:
                    result = _ORIGINAL_OPTS_SAVE(filename, *args, **kwargs) if filename is not None else _ORIGINAL_OPTS_SAVE(*args, **kwargs)
                except Exception as exc:
                    log_exception("opts.save", exc); raise
                after_cfg = read_json_file(cfg)
                changes = diff_snapshots(before_cfg, after_cfg)
                payload = {"filename": cfg, "config_changes": changes, "forge_try_reproduce_saved": after_cfg.get("forge_try_reproduce", "<missing>"), "neo_forge_try_reproduce_saved": after_cfg.get("neo_forge_try_reproduce", "<missing>")}
                sel = selected_preset_from_snapshot(old_snap); exp = EXPECTED_PRESETS.get(sel, {}).get("options", {})
                if sel and after_cfg.get("forge_try_reproduce", after_cfg.get("neo_forge_try_reproduce", "<missing>")) == "<missing>": payload["warning"] = "Preset changed in memory but not saved to config"
                missing_saved = [k for k in exp if after_cfg.get(k, "<missing>") != old_snap.get(k, "<missing>")]
                if missing_saved: payload["warning_dependent"] = "Config saved selected preset, but expected dependent options were not saved"; payload["dependent_keys"] = missing_saved
                log_event("opts.save", "shared.opts.save completed", payload)
                return result
            debug_opts_save._fncdbg_wrapped = True
            opts.save = debug_opts_save
            log_event("opts.save", "wrapped shared.opts.save", {})
    except Exception as exc:
        log_exception("patch_opts", exc)


def optioninfo_metadata(key, info):
    data = {"key": key}
    for attr in ["label", "default", "component", "component_args", "section", "onchange"]:
        try: data[attr] = getattr(info, attr)
        except Exception: data[attr] = "<unavailable>"
    return data


def register_option(key, default, label, component=None, component_args=None):
    try:
        from modules import shared
        from modules.options import OptionInfo
        info = OptionInfo(default, label, component=component, component_args=component_args or {}, section=("compatibility_debug", "Compatibility Debug"))
        shared.opts.add_option(key, info)
    except TypeError:
        from modules import shared
        from modules.options import OptionInfo
        info = OptionInfo(default, label, section=("compatibility_debug", "Compatibility Debug"))
        shared.opts.add_option(key, info)


def on_ui_settings():
    try:
        before_labels = set(opts_labels().keys()); before_data = dict(opts_data())
        log_event("ui_settings.before", "before registering debugger settings", {"label_count": len(before_labels), "data_count": len(before_data)})
        ok, gr, _ = try_import("gradio")
        checkbox = getattr(gr, "Checkbox", None) if ok else None; number = getattr(gr, "Number", None) if ok else None
        settings = [
            ("forge_neo_compat_debugger_enabled", True, "Forge Neo Compat Debugger: enabled", checkbox),
            ("forge_neo_compat_debugger_verbose", True, "Forge Neo Compat Debugger: verbose logging", checkbox),
            ("forge_neo_compat_debugger_log_txt", True, "Forge Neo Compat Debugger: write .txt log", checkbox),
            ("forge_neo_compat_debugger_log_jsonl", False, "Forge Neo Compat Debugger: write optional JSONL log", checkbox),
            ("forge_neo_compat_debugger_trace_sampling", True, "Forge Neo Compat Debugger: trace sampling", checkbox),
            ("forge_neo_compat_debugger_trace_components", True, "Forge Neo Compat Debugger: trace UI components", checkbox),
            ("forge_neo_compat_debugger_trace_option_set", True, "Forge Neo Compat Debugger: trace shared.opts.set", checkbox),
            ("forge_neo_compat_debugger_trace_config_save", True, "Forge Neo Compat Debugger: trace shared.opts.save", checkbox),
            ("forge_neo_compat_debugger_watch_interval_ms", 500, "Forge Neo Compat Debugger: watch interval ms", number),
        ]
        for key, default, label, comp in settings:
            if key not in opts_labels():
                register_option(key, default, label, comp)
        after_labels = set(opts_labels().keys())
        present = [k for k in WATCHED_KEYS if k in after_labels]; missing = [k for k in WATCHED_KEYS if k not in after_labels]
        payload = {"new_labels": sorted(after_labels - before_labels), "removed_labels": sorted(before_labels - after_labels), "watched_labels_present": present, "watched_labels_missing": missing}
        for key in present: payload.setdefault("watched_metadata", {})[key] = optioninfo_metadata(key, opts_labels().get(key))
        flags = []
        if "forge_try_reproduce" not in after_labels: flags.append("forge_try_reproduce does not exist")
        if "neo_forge_try_reproduce" in after_labels and "forge_try_reproduce" not in after_labels: flags.append("neo_forge_try_reproduce exists instead of forge_try_reproduce")
        if "neo_forge_try_reproduce" in after_labels and "forge_try_reproduce" in after_labels: flags.append("both forge_try_reproduce and neo_forge_try_reproduce exist")
        info = opts_labels().get("forge_try_reproduce")
        if info and not getattr(info, "onchange", None): flags.append("forge_try_reproduce exists but has no onchange callback")
        choices = None
        try:
            ca = getattr(info, "component_args", None); choices = ca() if callable(ca) else ca
            if isinstance(choices, dict): choices = choices.get("choices")
        except Exception: choices = None
        if choices:
            if [c for c in EXPECTED_CHOICES if c not in choices]: flags.append("radio choices are missing one or more expected choices")
            if list(choices) != EXPECTED_CHOICES: flags.append("radio choices do not match expected order")
        payload["flags"] = flags; payload["radio_choices"] = choices
        log_event("ui_settings.after", "after registering debugger settings", payload)
        patch_opts()
    except Exception as exc:
        log_exception("ui_settings", exc)


def component_payload(component=None, **kwargs):
    payload = {"component_class": type(component).__name__ if component is not None else None, "kwargs": kwargs}
    for attr in ["label", "elem_id", "value", "choices"]:
        try: payload[attr] = getattr(component, attr, kwargs.get(attr))
        except Exception: pass
    return payload


def relevant_component(payload):
    text = safe_json(payload).lower()
    needles = ["try to reproduce", "external software", "forge_try_reproduce", "neo_forge_try_reproduce", "diffusers", "comfyui", "webui 1.5", "invokeai", "easydiffusion", "drawthings"]
    return any(n in text for n in needles)


def on_before_component(component=None, **kwargs):
    try:
        if not enabled() or not trace_components(): return
        payload = component_payload(component, **kwargs)
        if relevant_component(payload): log_event("component.before", "relevant component before creation", payload)
    except Exception as exc: log_exception("component.before", exc)

def on_after_component(component=None, **kwargs):
    try:
        if not enabled() or not trace_components(): return
        payload = component_payload(component, **kwargs)
        if relevant_component(payload): log_event("component.after", "relevant component after creation", payload)
    except Exception as exc: log_exception("component.after", exc)


def detect_patch_targets():
    attrs = ["randn", "randn_like", "create_random_tensors", "get_sigmas_karras", "sample_dpmpp_sde", "sample_dpmpp_2m", "StableDiffusionProcessing", "StableDiffusionProcessingTxt2Img", "StableDiffusionProcessingImg2Img"]
    for modname in ["modules.rng", "modules.devices", "modules.sd_samplers", "modules.sd_samplers_kdiffusion", "modules.processing", "k_diffusion.sampling"]:
        ok, mod, tb = try_import(modname)
        if not ok:
            log_event("patch.detect", "Candidate module import failed", {"module": modname, "traceback": tb}); continue
        for attr in attrs:
            exists = hasattr(mod, attr)
            log_event("patch.detect", "Candidate target exists" if exists else "Candidate target missing", {"module": modname, "attr": attr, "callable": callable(getattr(mod, attr, None)) if exists else False})


def inspect_extensions():
    modules = []
    for name, mod in list(sys.modules.items()):
        if any(s in name.lower() for s in ["forge_neo_compat", "compat", "reproduce"]):
            modules.append({"module_name": name, "file": getattr(mod, "__file__", None), "has_apply_preset": hasattr(mod, "apply_preset"), "has_PRESETS": hasattr(mod, "PRESETS"), "has_DEFAULT_PRESETS": hasattr(mod, "DEFAULT_PRESETS"), "has_PATCH_RESULTS": hasattr(mod, "PATCH_RESULTS")})
    payload = {"modules": modules}
    for modname in ["forge_neo_compat.presets", "forge_neo_compat.patch_manager", "forge_neo_compat.options"]:
        ok, mod, tb = try_import(modname)
        payload[modname] = {"ok": ok, "traceback": tb}
        if ok:
            for attr in ["PRESET_CHOICES", "DEFAULT_PRESETS", "PATCH_RESULTS", "get_try_reproduce"]:
                if hasattr(mod, attr): payload[modname][attr] = getattr(mod, attr)
    log_event("patch.detect", "extension interaction diagnostics", payload)


def on_app_started(*args, **kwargs):
    try:
        shared = get_shared(); cfg = getattr(shared, "config_filename", None); labels = opts_labels()
        snap = snapshot_opts(); config = read_json_file(cfg)
        payload = {"config_filename": cfg, "config_exists": bool(cfg and Path(cfg).exists()), "has_opts": hasattr(shared, "opts"), "opts_type": str(type(getattr(shared, "opts", None))), "has_opts_data": hasattr(shared.opts, "data"), "has_opts_data_labels": hasattr(shared.opts, "data_labels"), "label_count": len(labels), "discovered_keys": discover_keys(), "watched_values": snap, "selected_preset": {"attr_forge_try_reproduce": get_opt_value("forge_try_reproduce"), "attr_neo_forge_try_reproduce": get_opt_value("neo_forge_try_reproduce"), "raw_data_forge_try_reproduce": opts_data().get("forge_try_reproduce", "<missing>"), "raw_data_neo_forge_try_reproduce": opts_data().get("neo_forge_try_reproduce", "<missing>"), "config_forge_try_reproduce": config.get("forge_try_reproduce", "<missing>"), "config_neo_forge_try_reproduce": config.get("neo_forge_try_reproduce", "<missing>")}}
        log_event("app_started", "app started diagnostics", payload)
        log_event("opts.snapshot", "startup watched option snapshot", snap)
        patch_opts(); detect_patch_targets(); inspect_extensions(); start_polling()
    except Exception as exc: log_exception("app_started", exc)


def start_polling():
    global _POLL_THREAD_STARTED, _LAST_SNAPSHOT
    if _POLL_THREAD_STARTED: return
    _POLL_THREAD_STARTED = True; _LAST_SNAPSHOT = snapshot_opts()
    def poll():
        global _LAST_SNAPSHOT
        while True:
            time.sleep(watch_interval())
            try:
                if not enabled(): continue
                cur = snapshot_opts(); changes = diff_snapshots(_LAST_SNAPSHOT or {}, cur)
                if changes:
                    log_event("opts.diff", "polling watcher detected option changes", {"changes": changes})
                    maybe_compare_expected_preset(_LAST_SNAPSHOT or {}, cur)
                    _LAST_SNAPSHOT = cur
            except Exception as exc:
                log_exception("poll", exc); time.sleep(5)
    threading.Thread(target=poll, name="fncdbg-option-poll", daemon=True).start()
    log_event("opts.diff", "started polling watcher", {"interval_seconds": watch_interval()})


def sampling_payload(p):
    snap = snapshot_opts(); sel = selected_preset_from_snapshot(snap)
    payload = {"selected_preset": sel, "options": {k: snap.get(k) for k in WATCHED_KEYS}, "p": {}}
    for attr in ["sampler_name", "scheduler", "steps", "seed", "subseed", "batch_size", "n_iter", "width", "height", "enable_hr", "hr_scale", "hr_upscaler", "extra_generation_params"]:
        try: payload["p"][attr] = getattr(p, attr)
        except Exception: payload["p"][attr] = "<unavailable>"
    payload["p"]["class"] = type(p).__name__
    for path in ["sd_model", "sd_model.forge_objects", "sd_model.forge_objects.unet", "sampler"]:
        obj = p
        try:
            for part in path.split("."): obj = getattr(obj, part)
            payload["p"][path] = str(type(obj))
        except Exception: payload["p"][path] = "<missing>"
    expected = EXPECTED_PRESETS.get(sel)
    warnings = []
    if expected and expected.get("runtime_force_noise_source") == "CPU" and snap.get("randn_source") != "CPU":
        warnings.append("Preset expects CPU runtime noise source, but observed randn_source is not CPU before sampling")
    if sel == "WebUI 1.5":
        bad = [k for k, v in EXPECTED_PRESETS[sel]["options"].items() if isinstance(v, bool) and snap.get(k) is not v]
        if bad: warnings.append("Preset expects WebUI 1.5 legacy flags, but one or more flags are false before sampling")
    payload["warnings"] = warnings
    return payload

# callbacks registration
ok, callbacks, _ = try_import("modules.script_callbacks")
if ok:
    for name, fn in [("on_ui_settings", on_ui_settings), ("on_app_started", on_app_started), ("on_before_component", on_before_component), ("on_after_component", on_after_component)]:
        cb = getattr(callbacks, name, None)
        if cb:
            try: cb(fn); log_event("import", f"registered {name}", {})
            except Exception as exc: log_exception(f"register {name}", exc)
        elif name in ("on_before_component", "on_after_component"):
            log_event("component.before" if "before" in name else "component.after", f"{name} unavailable", {})
else:
    log_event("error", "modules.script_callbacks unavailable; callbacks not registered", {})

ok, scripts_mod, _ = try_import("modules.scripts")
BaseScript = getattr(scripts_mod, "Script", object) if ok else object

class ForgeNeoCompatDebuggerScript(BaseScript):
    def title(self): return "Forge Neo Compat Debugger"
    def show(self, is_img2img): return False
    def before_process(self, p, *args):
        if enabled() and trace_sampling(): log_event("sampling.before", "before_process sampling diagnostics", sampling_payload(p))
    def process(self, p, *args):
        if enabled() and trace_sampling(): log_event("sampling.before", "process sampling diagnostics", sampling_payload(p))
    def process_before_every_sampling(self, p, *args, **kwargs):
        if enabled() and trace_sampling(): log_event("sampling.before", "process_before_every_sampling diagnostics", sampling_payload(p))
    def postprocess(self, p, processed, *args):
        if enabled() and trace_sampling(): log_event("sampling.after", "postprocess sampling diagnostics", sampling_payload(p))
