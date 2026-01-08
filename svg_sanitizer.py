"""
SVG Sanitizer for Radio Registry
Removes dangerous elements and attributes from SVG files to prevent XSS attacks.
Uses proper XML parsing instead of regex for reliable sanitization.
"""
import re
from typing import Optional
from xml.etree import ElementTree as ET

try:
    import defusedxml.ElementTree as DefusedET
    HAS_DEFUSEDXML = True
except ImportError:
    HAS_DEFUSEDXML = False


# SVG namespace
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Allowed SVG elements (whitelist approach) - lowercase
ALLOWED_ELEMENTS = frozenset([
    # Container elements
    'svg', 'g', 'defs', 'symbol', 'use', 'marker', 'clippath', 'mask', 'pattern',
    # Shape elements
    'circle', 'ellipse', 'line', 'polygon', 'polyline', 'rect', 'path',
    # Text elements
    'text', 'tspan', 'textpath',
    # Gradient elements
    'lineargradient', 'radialgradient', 'stop',
    # Filter elements (basic)
    'filter', 'fegaussianblur', 'feoffset', 'feblend', 'fecolormatrix',
    'fecomponenttransfer', 'fecomposite', 'feflood', 'femerge', 'femergenode',
    # Other safe elements
    'title', 'desc',
])

# Allowed attributes (whitelist approach) - lowercase
ALLOWED_ATTRIBUTES = frozenset([
    # Core attributes
    'id', 'class', 'style', 'transform', 'lang', 'tabindex',
    # Presentation attributes
    'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width',
    'stroke-opacity', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray',
    'stroke-dashoffset', 'stroke-miterlimit', 'opacity', 'visibility',
    'display', 'overflow', 'clip', 'clip-path', 'clip-rule', 'mask',
    'filter', 'color', 'color-interpolation', 'color-interpolation-filters',
    'flood-color', 'flood-opacity', 'lighting-color', 'stop-color', 'stop-opacity',
    # Positioning
    'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
    'width', 'height', 'dx', 'dy', 'rotate', 'textlength', 'lengthadjust',
    # Path data
    'd', 'pathlength', 'points',
    # Viewbox and dimensions
    'viewbox', 'preserveaspectratio',
    # Gradients
    'gradientunits', 'gradienttransform', 'spreadmethod', 'offset',
    'fx', 'fy', 'fr',
    # Filters
    'filterunits', 'primitiveunits', 'result', 'in', 'in2', 'mode',
    'stddeviation', 'type', 'values', 'operator', 'k1', 'k2', 'k3', 'k4',
    # Text
    'font-family', 'font-size', 'font-style', 'font-weight', 'font-variant',
    'text-anchor', 'dominant-baseline', 'alignment-baseline', 'baseline-shift',
    'letter-spacing', 'word-spacing', 'text-decoration', 'writing-mode',
    # Other
    'marker-start', 'marker-mid', 'marker-end', 'markerwidth', 'markerheight',
    'refx', 'refy', 'orient', 'markerunits', 'patternunits', 'patterntransform',
    'patterncontentunits',
    # Namespace declarations
    'xmlns', 'version',
])

# Dangerous patterns to remove from style attributes
DANGEROUS_STYLE_PATTERNS = [
    re.compile(r'javascript:', re.IGNORECASE),
    re.compile(r'expression\s*\(', re.IGNORECASE),
    re.compile(r'url\s*\(\s*["\']?\s*javascript:', re.IGNORECASE),
    re.compile(r'url\s*\(\s*["\']?\s*data:(?!image/)', re.IGNORECASE),
    re.compile(r'-moz-binding', re.IGNORECASE),
    re.compile(r'behavior\s*:', re.IGNORECASE),
]


def _get_local_name(tag: str) -> str:
    """Extract local name from potentially namespaced tag"""
    if tag.startswith('{'):
        return tag.split('}', 1)[1].lower()
    return tag.lower()


def _sanitize_style(style: str) -> str:
    """Sanitize a style attribute value"""
    for pattern in DANGEROUS_STYLE_PATTERNS:
        style = pattern.sub('', style)
    return style


def _sanitize_url(url: str) -> Optional[str]:
    """Sanitize a URL attribute, return None if dangerous"""
    url = url.strip()
    url_lower = url.lower()

    # Allow relative URLs (references within SVG)
    if url.startswith('#'):
        return url

    # Block dangerous schemes
    dangerous_schemes = ['javascript:', 'data:', 'vbscript:', 'file:']
    for scheme in dangerous_schemes:
        if url_lower.startswith(scheme):
            return None

    # Allow http/https for images
    if url_lower.startswith(('http://', 'https://')):
        return url

    # Block everything else for security
    return None


def _sanitize_element(elem: ET.Element, parent: Optional[ET.Element] = None) -> bool:
    """
    Recursively sanitize an element and its children.
    Returns True if element should be kept, False if it should be removed.
    """
    local_name = _get_local_name(elem.tag)

    # Remove disallowed elements entirely
    if local_name not in ALLOWED_ELEMENTS:
        return False

    # Sanitize attributes
    attrs_to_remove = []
    for attr, value in elem.attrib.items():
        attr_local = _get_local_name(attr).lower()

        # Remove event handlers (onclick, onload, etc.)
        if attr_local.startswith('on'):
            attrs_to_remove.append(attr)
            continue

        # Handle href/xlink:href specially
        if attr_local == 'href' or attr.endswith('}href'):
            sanitized_url = _sanitize_url(value)
            if sanitized_url is None:
                attrs_to_remove.append(attr)
            else:
                elem.attrib[attr] = sanitized_url
            continue

        # Handle style attribute
        if attr_local == 'style':
            sanitized_style = _sanitize_style(value)
            if sanitized_style.strip():
                elem.attrib[attr] = sanitized_style
            else:
                attrs_to_remove.append(attr)
            continue

        # Remove non-whitelisted attributes
        if attr_local not in ALLOWED_ATTRIBUTES:
            attrs_to_remove.append(attr)

    for attr in attrs_to_remove:
        del elem.attrib[attr]

    # Recursively sanitize children
    children_to_remove = []
    for child in elem:
        if not _sanitize_element(child, elem):
            children_to_remove.append(child)

    for child in children_to_remove:
        elem.remove(child)

    return True


def sanitize_svg(svg_content: bytes) -> Optional[bytes]:
    """
    Sanitize SVG content to remove XSS vectors.

    Args:
        svg_content: Raw SVG bytes

    Returns:
        Sanitized SVG bytes, or None if SVG cannot be parsed
    """
    try:
        # Decode content
        try:
            content = svg_content.decode('utf-8')
        except UnicodeDecodeError:
            content = svg_content.decode('latin-1')

        # Parse SVG using defusedxml if available (protects against XML attacks)
        if HAS_DEFUSEDXML:
            root = DefusedET.fromstring(content)
        else:
            root = ET.fromstring(content)

        # Verify it's an SVG
        root_local = _get_local_name(root.tag)
        if root_local != 'svg':
            return None

        # Sanitize the entire tree
        _sanitize_element(root)

        # Ensure xmlns is set
        if 'xmlns' not in root.attrib:
            root.attrib['xmlns'] = SVG_NS

        # Serialize back to bytes
        # Use 'unicode' to get string, then encode
        result = ET.tostring(root, encoding='unicode')

        return result.encode('utf-8')

    except ET.ParseError:
        # If parsing fails, return None to indicate failure
        return None
    except Exception:
        # If sanitization fails, return None
        return None


def is_svg_safe(svg_content: bytes) -> bool:
    """
    Check if SVG content appears to be safe (no obvious XSS vectors).
    This is a quick check, sanitize_svg() should still be used.

    Args:
        svg_content: Raw SVG bytes

    Returns:
        True if no obvious dangers detected
    """
    try:
        content = svg_content.decode('utf-8', errors='ignore').lower()

        dangerous_patterns = [
            '<script',
            'javascript:',
            'onerror=',
            'onload=',
            'onclick=',
            'onmouseover=',
            'onfocus=',
            'onblur=',
            '<foreignobject',
            'data:text/html',
            '-moz-binding',
            'expression(',
            'behavior:',
        ]

        for pattern in dangerous_patterns:
            if pattern in content:
                return False

        return True
    except Exception:
        return False
