from .nav import section as current_section


def section(request):
    """Раздел, в котором человек сейчас, — для подсветки пункта меню (`core/nav.py`)."""
    return {"section": current_section(request)}
