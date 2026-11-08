"""Small inline line icons (24x24, stroke = current text colour). No icon font or download needed."""

_PATHS = {
    "logo": '<rect x="3" y="8" width="18" height="8" rx="4"/><path d="M12 8v8"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "sample": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "speaker": '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12"/>',
    "check_user": '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 11l2 2 4-4"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    "wifi_off": '<path d="M2 8a16 16 0 0 1 5-3M22 8a16 16 0 0 0-9-3.9M5 12a10 10 0 0 1 4-2.5M19 12a10 10 0 0 0-2-1.5M8.5 15.5a5 5 0 0 1 7 0M12 19h.01M3 3l18 18"/>',
    "block": '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "sun_high": '<circle cx="12" cy="14" r="4"/><path d="M12 4v3M5 9l2 2M19 9l-2 2M3 20h18"/>',
    "sunset": '<path d="M7 18a5 5 0 0 1 10 0"/><path d="M12 6v4M4.9 11.9l1.4 1.4M19.1 11.9l-1.4 1.4M2 18h20M3 22h18"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "phone": '<path d="M5 3h4l2 5-3 2a11 11 0 0 0 6 6l2-3 5 2v4a2 2 0 0 1-2 2A17 17 0 0 1 3 5a2 2 0 0 1 2-2z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def icon(name: str, size: int = 22, cls: str = "i") -> str:
    return (f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{_PATHS[name]}</svg>')
