/*
 * report.js - interactive behaviour for the generated test reports.
 * =================================================================
 *
 * The one hand-written browser script in this project. Three consumers embed
 * or serve this exact text, unchanged, unbundled and untranspiled:
 *
 *   1. app/reporting/html_report.py inlines it into the single self-contained
 *      page written to target/cucumber-reports.html.
 *   2. app/reporting/pretty_reports.py emits it beside the pages of the
 *      target/cucumber tree, at the same relative name js/report.js.
 *   3. app/__init__.py serves it over Flask's package-relative static folder,
 *      referenced by the view templates as
 *      url_for('static', filename='js/report.js').
 *
 * Four consequences shape every line below.
 *
 *   - It is inlined into an HTML page. It therefore contains no sequence that
 *     could terminate or corrupt the surrounding markup, and no module syntax
 *     of any kind: it is always evaluated as a classic script, never as a
 *     module, so it neither imports nor exports anything.
 *   - The reports are opened straight from a Jenkins workspace over file://.
 *     Nothing here assumes a server, an origin, a base URL or an absolute
 *     path, and no code path can issue a network request: the lightbox only
 *     ever accepts a source that is already an inline data: URI (see below).
 *   - It may be evaluated before or after DOMContentLoaded, and possibly twice
 *     on one page. It therefore binds delegated listeners once, guards
 *     re-evaluation with a single namespaced global, and defers only its
 *     initial ARIA sync.
 *   - It has no dependency whatsoever: no library, no polyfill, no build step,
 *     no vendored global. The libraries that ship in app/static/vendor are
 *     third-party generator output for the emitted pages and are deliberately
 *     never referenced here, because neither the self-contained artifact nor
 *     the Flask views load them.
 *
 * SCOPE - exactly three behaviours, and no others:
 *
 *   1. Filtering result rows by status.
 *   2. Expanding and collapsing detail regions, including expand-all and
 *      collapse-all where the markup offers those controls.
 *   3. The screenshot lightbox, over failure screenshots that are already
 *      present in the page as inline data: URIs.
 *
 * There is deliberately no sorting, no charting, no persistence, no
 * analytics, no clipboard support, no theming switch, no keyboard-shortcut
 * layer, no address-bar state syncing and no recomputation of any count or
 * summary. This script presents results; it never changes them, and it can
 * never start a test run.
 *
 * -----------------------------------------------------------------------
 * THE SHARED HOOK CONTRACT
 * -----------------------------------------------------------------------
 * This script only ever toggles state. app/static/css/main.css owns how that
 * state renders, and the templates own the markup - the artifact templates,
 * the pretty templates and the Flask views all share the same three partials
 * (status badge, step row, screenshot lightbox), so one vocabulary has to
 * serve all three. Publishing the whole contract here is what lets the
 * stylesheet and the partials converge on it.
 *
 * Selection is by data attribute only. The generator classes carried by the
 * emitted pretty pages (passed, step, element, collapsable-control, chevron,
 * collapse, panel and the rest) are never selected on and never modified:
 * they drive the vendored generator's own collapse widget, and touching them
 * would fight it.
 *
 * SELECTION ATTRIBUTES
 * --------------------
 *   data-report-root
 *       Container wrapping report content. Query scope: filtering and
 *       expand-all/collapse-all apply within the clicked element's closest
 *       root, falling back to the document when no root ancestor exists.
 *       Sibling roots are therefore independent of one another.
 *
 *   data-report-filter="<status>" | "all"
 *       A filter control, expected to be a non-navigating <button
 *       type="button">. Toggles <status> in the shown-set for its scope.
 *       "all" is the one reserved value and clears the set. A value that is
 *       empty or whitespace-only is treated as "all", because no element can
 *       carry an empty status and the alternative would hide everything.
 *
 *   data-report-filterable
 *       Marks an element the status filter may hide. Hiding it hides its
 *       whole subtree, so no per-descendant bookkeeping is needed.
 *
 *   data-report-status="<status>"
 *       The status of that element, compared case-insensitively and after
 *       trimming. Values are opaque strings: passed, failed, skipped, pending
 *       and undefined are what occur in practice, but no closed list is
 *       hard-coded, so a status added later needs no change here. A
 *       filterable element with no status, or a blank one, is never hidden by
 *       a status filter.
 *
 *   data-report-toggle
 *       A control that toggles one detail region.
 *
 *   data-report-toggle-target="<id>"
 *       Optional explicit target for that control. aria-controls is preferred
 *       and is tried first.
 *
 *   data-report-detail
 *       The collapsible region that a toggle shows and hides.
 *
 *   data-report-toggle-all="expand" | "collapse"
 *       A control that expands or collapses every detail region in its scope.
 *       Where the markup offers no such control the feature simply does not
 *       exist on that page, which is expected rather than an error.
 *
 *   data-report-screenshot
 *       The lightbox trigger. An optional value supplies the image source;
 *       when it is empty the authored src of an <img> inside the trigger is
 *       used instead.
 *
 *   data-report-screenshot-name
 *       Optional caption text for that trigger - the embedding's name, which
 *       is the scenario name.
 *
 *   data-report-lightbox
 *       The lightbox container, once per page. The overlay itself.
 *
 *   data-report-lightbox-image
 *       An <img> inside the container. Receives the source.
 *
 *   data-report-lightbox-caption
 *       Optional element inside the container. Receives the caption text.
 *
 *   data-report-lightbox-close
 *       A control inside the container that closes it.
 *
 *   data-report-lightbox-backdrop
 *       Optional explicit backdrop. A click whose target is the container
 *       itself is also treated as a backdrop click; a click inside the image
 *       never is.
 *
 * STATE CLASSES - exactly three, and no fourth is permitted
 * ---------------------------------------------------------
 *   report-is-collapsed        on [data-report-detail]      detail hidden
 *   report-is-filtered-out     on [data-report-filterable]  row hidden by the
 *                                                           status filter
 *   report-lightbox-is-open    on [data-report-lightbox]     overlay visible
 *
 * Active-filter styling is expressed through aria-pressed="true|false" on the
 * filter control, which the stylesheet can select on. That keeps the class set
 * at three and is also the accessible form.
 *
 * ARIA MIRRORS
 * ------------
 * State is mirrored into ARIA rather than into inline style or the hidden
 * attribute, because the stylesheet owns rendering:
 *   aria-expanded on the toggle control, aria-hidden on the detail region,
 *   aria-pressed on the filter control, and on the open overlay
 *   role="dialog", aria-modal="true" and aria-hidden="false" (role and
 *   aria-modal only when the markup has not already set them).
 *
 * REQUIREMENTS ON THE MARKUP
 * --------------------------
 *   - The authored default is expanded and unfiltered. Initialization sets
 *     ARIA attributes to match that authored state and changes no class, so a
 *     reader with scripting disabled loses interactivity but never content.
 *     The stylesheet must render all three states, and must render their
 *     absence as the fully expanded, unfiltered page.
 *   - Hook controls must be non-navigating - <button type="button"> - because
 *     this script never suppresses an authored default action. That is
 *     deliberate: a hook that swallowed default actions would also swallow a
 *     genuine link inside a row.
 *   - Every hook is optional. A missing control, container, region or
 *     embedding is a silent no-op, never an error, so a page with no failures
 *     and no screenshots - equally an index or error page that merely extends
 *     the base template - runs this script with no diagnostic output at all.
 */
(function () {
    'use strict';

    /* -------------------------------------------------------------------
     * Environment and idempotence guards
     * ------------------------------------------------------------------- */

    var globalScope = typeof window !== 'undefined' ? window : null;
    var doc = globalScope && globalScope.document ? globalScope.document : null;

    /* No window and no document means there is nothing to enhance. Bailing
     * out keeps this text harmless if it is ever evaluated outside a page. */
    if (!globalScope || !doc) {
        return;
    }

    var GLOBAL_NAME = 'ReportUI';
    var VERSION = '1.0.0.dev0';

    /* The idempotence guard. This text may be inlined into a page more than
     * once - two partials, or an artifact template that includes it alongside
     * a view. Each copy is wrapped in its own function scope, so the only
     * shared signal is the global: if it is already there, a previous copy has
     * already bound the delegated listeners and this copy must add nothing. */
    if (globalScope[GLOBAL_NAME]) {
        return;
    }

    /* -------------------------------------------------------------------
     * The three state classes, and the attributes selection is built from
     * ------------------------------------------------------------------- */

    var CLASS_COLLAPSED = 'report-is-collapsed';
    var CLASS_FILTERED_OUT = 'report-is-filtered-out';
    var CLASS_LIGHTBOX_OPEN = 'report-lightbox-is-open';

    var ATTR_ROOT = 'data-report-root';
    var ATTR_FILTER = 'data-report-filter';
    var ATTR_FILTERABLE = 'data-report-filterable';
    var ATTR_STATUS = 'data-report-status';
    var ATTR_TOGGLE = 'data-report-toggle';
    var ATTR_TOGGLE_TARGET = 'data-report-toggle-target';
    var ATTR_TOGGLE_ALL = 'data-report-toggle-all';
    var ATTR_DETAIL = 'data-report-detail';
    var ATTR_SCREENSHOT = 'data-report-screenshot';
    var ATTR_SCREENSHOT_NAME = 'data-report-screenshot-name';
    var ATTR_LIGHTBOX = 'data-report-lightbox';
    var ATTR_LIGHTBOX_IMAGE = 'data-report-lightbox-image';
    var ATTR_LIGHTBOX_CAPTION = 'data-report-lightbox-caption';
    var ATTR_LIGHTBOX_CLOSE = 'data-report-lightbox-close';
    var ATTR_LIGHTBOX_BACKDROP = 'data-report-lightbox-backdrop';

    var ATTR_ARIA_CONTROLS = 'aria-controls';
    var ATTR_ARIA_EXPANDED = 'aria-expanded';
    var ATTR_ARIA_HIDDEN = 'aria-hidden';
    var ATTR_ARIA_MODAL = 'aria-modal';
    var ATTR_ARIA_PRESSED = 'aria-pressed';

    /* The reserved filter value, and the only image source this script will
     * ever hand to an img element. */
    var FILTER_ALL = 'all';
    var DATA_IMAGE_PREFIX = 'data:image/';

    /* Selectors are derived from the attribute names above so the two can
     * never drift apart. An attribute selector matches an exact attribute
     * name, so [data-report-toggle] does not also match a control that
     * carries data-report-toggle-all or data-report-toggle-target. */
    function attributeSelector(attributeName) {
        return '[' + attributeName + ']';
    }

    var SEL_ROOT = attributeSelector(ATTR_ROOT);
    var SEL_FILTER = attributeSelector(ATTR_FILTER);
    var SEL_FILTERABLE = attributeSelector(ATTR_FILTERABLE);
    var SEL_TOGGLE = attributeSelector(ATTR_TOGGLE);
    var SEL_TOGGLE_ALL = attributeSelector(ATTR_TOGGLE_ALL);
    var SEL_DETAIL = attributeSelector(ATTR_DETAIL);
    var SEL_SCREENSHOT = attributeSelector(ATTR_SCREENSHOT);
    var SEL_LIGHTBOX = attributeSelector(ATTR_LIGHTBOX);
    var SEL_LIGHTBOX_IMAGE = attributeSelector(ATTR_LIGHTBOX_IMAGE);
    var SEL_LIGHTBOX_CAPTION = attributeSelector(ATTR_LIGHTBOX_CAPTION);
    var SEL_LIGHTBOX_CLOSE = attributeSelector(ATTR_LIGHTBOX_CLOSE);
    var SEL_LIGHTBOX_BACKDROP = attributeSelector(ATTR_LIGHTBOX_BACKDROP);

    /* -------------------------------------------------------------------
     * Small, defensive DOM helpers
     *
     * Every lookup in this file goes through one of these, and each one
     * tolerates null, a non-element node and a host object that lacks the
     * method. That is what makes "every hook is optional" true by
     * construction rather than by discipline at each call site.
     * ------------------------------------------------------------------- */

    var ELEMENT_NODE = 1;

    function isElement(node) {
        return !!node && node.nodeType === ELEMENT_NODE;
    }

    function matchesSelector(node, selectorText) {
        if (!isElement(node) || typeof node.matches !== 'function') {
            return false;
        }
        return node.matches(selectorText);
    }

    /* Walks from the node itself outwards, which is how every click is
     * routed. A non-element target - a text node in some engines - is
     * promoted to its parent element first. */
    function closestMatch(node, selectorText) {
        var current = isElement(node) ? node : (node && node.parentElement) || null;
        if (isElement(current) && typeof current.closest === 'function') {
            return current.closest(selectorText);
        }
        while (isElement(current)) {
            if (matchesSelector(current, selectorText)) {
                return current;
            }
            current = current.parentElement;
        }
        return null;
    }

    function queryAll(scope, selectorText) {
        if (!scope || typeof scope.querySelectorAll !== 'function') {
            return [];
        }
        return scope.querySelectorAll(selectorText);
    }

    function queryOne(scope, selectorText) {
        if (!scope || typeof scope.querySelector !== 'function') {
            return null;
        }
        return scope.querySelector(selectorText);
    }

    /* A plain indexed walk, so a live or static node list and a plain array
     * are all handled without converting anything. */
    function each(collection, callback) {
        if (!collection) {
            return;
        }
        var length = collection.length;
        for (var index = 0; index < length; index += 1) {
            callback(collection[index]);
        }
    }

    function readAttribute(element, attributeName) {
        if (!isElement(element) || typeof element.getAttribute !== 'function') {
            return null;
        }
        return element.getAttribute(attributeName);
    }

    function writeAttribute(element, attributeName, value) {
        if (!isElement(element) || typeof element.setAttribute !== 'function') {
            return;
        }
        element.setAttribute(attributeName, value);
    }

    function hasAttribute(element, attributeName) {
        return isElement(element) &&
            typeof element.hasAttribute === 'function' &&
            element.hasAttribute(attributeName);
    }

    /* Status and filter values are compared after trimming and lowercasing,
     * and nowhere else in this file is a status inspected. */
    function normalize(value) {
        return typeof value === 'string' ? value.trim().toLowerCase() : '';
    }

    function hasClass(element, className) {
        return isElement(element) && !!element.classList && element.classList.contains(className);
    }

    function setClass(element, className, present) {
        if (!isElement(element) || !element.classList) {
            return;
        }
        if (present) {
            element.classList.add(className);
        } else {
            element.classList.remove(className);
        }
    }

    function isConnected(element) {
        if (!isElement(element)) {
            return false;
        }
        if (typeof element.isConnected === 'boolean') {
            return element.isConnected;
        }
        return typeof doc.contains === 'function' && doc.contains(element);
    }

    function safeFocus(element) {
        if (!isElement(element) || typeof element.focus !== 'function') {
            return;
        }
        try {
            element.focus();
        } catch (error) {
            /* Moving focus is presentation, never correctness. If the host
             * refuses - a detached or inert element - the overlay is already
             * in the right visual state and the interaction stands. */
            void error;
        }
    }

    /* The scope of a filtering or expand-all action: the clicked element's
     * closest root, or the whole document when the markup declares none. */
    function resolveScope(element) {
        return closestMatch(element, SEL_ROOT) || doc;
    }

    /* -------------------------------------------------------------------
     * Behaviour 1: filtering result rows by status
     *
     * The model is a show-set per scope, and an EMPTY SET MEANS SHOW
     * EVERYTHING. That is what lets initialization change no DOM state: the
     * authored page, with no control pressed, is already the unfiltered view,
     * so this script only ever adds state to it.
     *
     * The set is a plain array of normalized status strings. Membership is a
     * linear scan over at most a handful of statuses, which is cheaper than
     * the alternative and depends on nothing beyond ES5.
     * ------------------------------------------------------------------- */

    /* Per-scope storage keyed on the scope object itself - a root element, or
     * the document when the markup declares no root. A weak map is used when
     * the host provides one so nothing is retained beyond the page; the paired
     * arrays are an equivalent fallback, holding at most one entry per root. */
    function createScopeStore() {
        if (typeof WeakMap === 'function') {
            var weakStore = new WeakMap();
            return {
                get: function (scope) {
                    return weakStore.get(scope) || null;
                },
                set: function (scope, value) {
                    weakStore.set(scope, value);
                }
            };
        }
        var scopes = [];
        var values = [];
        return {
            get: function (scope) {
                var index = scopes.indexOf(scope);
                return index === -1 ? null : values[index];
            },
            set: function (scope, value) {
                var index = scopes.indexOf(scope);
                if (index === -1) {
                    scopes.push(scope);
                    values.push(value);
                } else {
                    values[index] = value;
                }
            }
        };
    }

    var shownStatusStore = createScopeStore();

    /* Returns the scope's show-set, creating it only when a click needs to
     * record something. Read paths pass false, so merely rendering a page
     * stores nothing at all. */
    function shownStatusesFor(scope, createIfMissing) {
        if (!scope) {
            return null;
        }
        var shown = shownStatusStore.get(scope);
        if (!shown && createIfMissing) {
            shown = [];
            shownStatusStore.set(scope, shown);
        }
        return shown || null;
    }

    /* Applies the scope's show-set to every filterable element in it. Hiding
     * an element hides its whole subtree, so descendants need no bookkeeping.
     * An element with no status, or a blank one, is never hidden by a status
     * filter - only by a filter on a status it actually carries. */
    function applyStatusFilter(scope) {
        var shown = shownStatusesFor(scope, false);
        var showEverything = !shown || shown.length === 0;
        each(queryAll(scope, SEL_FILTERABLE), function (element) {
            if (showEverything) {
                setClass(element, CLASS_FILTERED_OUT, false);
                return;
            }
            var status = normalize(readAttribute(element, ATTR_STATUS));
            if (status === '') {
                setClass(element, CLASS_FILTERED_OUT, false);
                return;
            }
            setClass(element, CLASS_FILTERED_OUT, shown.indexOf(status) === -1);
        });
    }

    /* A control is pressed exactly when its status is in the show-set of its
     * own scope, which is what keeps sibling roots independent. The reserved
     * "all" value, and an empty one, are never members and so never pressed. */
    function isFilterPressed(control) {
        var value = normalize(readAttribute(control, ATTR_FILTER));
        if (value === '' || value === FILTER_ALL) {
            return false;
        }
        var shown = shownStatusesFor(resolveScope(control), false);
        return !!shown && shown.indexOf(value) !== -1;
    }

    function syncFilterControls(scope) {
        each(queryAll(scope, SEL_FILTER), function (control) {
            writeAttribute(control, ATTR_ARIA_PRESSED, isFilterPressed(control) ? 'true' : 'false');
        });
    }

    function handleFilterClick(control) {
        var scope = resolveScope(control);
        var shown = shownStatusesFor(scope, true);
        var value = normalize(readAttribute(control, ATTR_FILTER));

        if (value === '' || value === FILTER_ALL) {
            /* Emptied in place so the stored reference stays valid. */
            shown.length = 0;
        } else {
            var position = shown.indexOf(value);
            if (position === -1) {
                shown.push(value);
            } else {
                shown.splice(position, 1);
            }
        }

        applyStatusFilter(scope);
        syncFilterControls(scope);
    }

    /* -------------------------------------------------------------------
     * Behaviour 2: expanding and collapsing detail regions
     * ------------------------------------------------------------------- */

    /* aria-controls holds an id reference LIST, so the value is tokenized and
     * the first token that resolves wins. */
    function firstElementById(idReferenceList) {
        if (typeof idReferenceList !== 'string') {
            return null;
        }
        var tokens = idReferenceList.trim().split(/\s+/);
        for (var index = 0; index < tokens.length; index += 1) {
            if (tokens[index] !== '') {
                var found = doc.getElementById(tokens[index]);
                if (found) {
                    return found;
                }
            }
        }
        return null;
    }

    /* Resolution order, fixed: aria-controls, then the explicit target
     * attribute, then the first detail region inside the control's own row.
     * The row is the closest filterable element, falling back to the control's
     * parent. Nothing resolving is a silent no-op at the call site. */
    function resolveDetailRegion(control) {
        var region = firstElementById(readAttribute(control, ATTR_ARIA_CONTROLS));
        if (!region) {
            region = firstElementById(readAttribute(control, ATTR_TOGGLE_TARGET));
        }
        if (region) {
            return region;
        }
        var row = closestMatch(control, SEL_FILTERABLE) ||
            (isElement(control) ? control.parentElement : null);
        return queryOne(row, SEL_DETAIL);
    }

    /* The region is never removed, re-parented or rewritten - only classed
     * and described. The stylesheet decides what "collapsed" looks like. */
    function setCollapsed(region, collapsed) {
        if (!isElement(region)) {
            return;
        }
        setClass(region, CLASS_COLLAPSED, collapsed);
        writeAttribute(region, ATTR_ARIA_HIDDEN, collapsed ? 'true' : 'false');
    }

    /* Re-derives aria-expanded for every toggle in scope from the region each
     * one actually points at. One code path serves the single toggle, both
     * toggle-all controls and the initial sync, so several controls addressing
     * one region can never disagree. */
    function syncToggleControls(scope) {
        each(queryAll(scope, SEL_TOGGLE), function (control) {
            var region = resolveDetailRegion(control);
            if (region) {
                writeAttribute(
                    control,
                    ATTR_ARIA_EXPANDED,
                    hasClass(region, CLASS_COLLAPSED) ? 'false' : 'true'
                );
            }
        });
    }

    function handleToggleClick(control) {
        var region = resolveDetailRegion(control);
        if (!region) {
            return;
        }
        setCollapsed(region, !hasClass(region, CLASS_COLLAPSED));
        /* The clicked control is described immediately, then every other
         * control in scope is brought into line with it. */
        writeAttribute(
            control,
            ATTR_ARIA_EXPANDED,
            hasClass(region, CLASS_COLLAPSED) ? 'false' : 'true'
        );
        syncToggleControls(resolveScope(control));
    }

    function handleToggleAllClick(control) {
        var mode = normalize(readAttribute(control, ATTR_TOGGLE_ALL));
        if (mode !== 'expand' && mode !== 'collapse') {
            return;
        }
        var scope = resolveScope(control);
        var collapse = mode === 'collapse';
        each(queryAll(scope, SEL_DETAIL), function (region) {
            setCollapsed(region, collapse);
        });
        syncToggleControls(scope);
    }


    /* -------------------------------------------------------------------
     * Behaviour 3: the screenshot lightbox
     *
     * A failure screenshot is captured once, on failure, before the driver is
     * quit, and travels in the results as a base64 PNG embedding with a mime
     * type and the scenario name. Both HTML outputs render it as an inline
     * data: URI, so by the time this script sees it the image is ALREADY IN
     * THE PAGE and opening the overlay is pure presentation.
     *
     * That is why the source is validated before use: only an inline
     * data:image/ value is ever accepted. Anything else - an absolute URL, a
     * protocol-relative one, or a relative path - is rejected and the overlay
     * does not open. There is consequently no code path in this file that can
     * cause the browser to request anything, which is what makes the reports
     * safe to read straight from a workspace with no network at all.
     *
     * There is deliberately no capture control and no enable switch here: the
     * documentation's claim of screenshots for passing tests describes an
     * intent the implementation never had.
     * ------------------------------------------------------------------- */

    var openOverlay = null;
    var focusBeforeOverlay = null;
    var overlayOwnsImageAlt = false;

    /* One overlay per page. A root-local one is preferred when the markup
     * happens to provide it, so a page with sibling roots behaves sensibly. */
    function findOverlay(origin) {
        var root = closestMatch(origin, SEL_ROOT);
        return (root && queryOne(root, SEL_LIGHTBOX)) || queryOne(doc, SEL_LIGHTBOX);
    }

    /* The trigger's own value wins; otherwise the authored src of an img
     * inside it - or of the trigger itself, when the trigger IS the image.
     *
     * getAttribute is used rather than the src property on purpose: the
     * property returns a value already resolved against the document's own
     * address, which would turn a relative path into an absolute one and let
     * it slip past the data: check below. The authored attribute is what the
     * template actually wrote. */
    function resolveScreenshotSource(trigger) {
        var explicit = readAttribute(trigger, ATTR_SCREENSHOT);
        if (typeof explicit === 'string' && explicit.trim() !== '') {
            return explicit.trim();
        }
        var image = isElement(trigger) && trigger.localName === 'img'
            ? trigger
            : queryOne(trigger, 'img');
        var authored = readAttribute(image, 'src');
        return typeof authored === 'string' ? authored.trim() : '';
    }

    function isInlineImageSource(value) {
        if (typeof value !== 'string') {
            return false;
        }
        var trimmed = value.trim();
        if (trimmed === '') {
            return false;
        }
        return trimmed.slice(0, DATA_IMAGE_PREFIX.length).toLowerCase() === DATA_IMAGE_PREFIX;
    }

    /* Nothing is mutated until the overlay, its image and the source have all
     * been resolved and validated, so a rejected source leaves the page
     * exactly as it was. */
    function openLightbox(trigger) {
        var overlay = findOverlay(trigger);
        if (!overlay) {
            return;
        }
        var image = queryOne(overlay, SEL_LIGHTBOX_IMAGE);
        if (!image) {
            return;
        }
        var source = resolveScreenshotSource(trigger);
        if (!isInlineImageSource(source)) {
            return;
        }

        var nameAttribute = readAttribute(trigger, ATTR_SCREENSHOT_NAME);
        /* Never render the string "null" or "undefined" as visible text. */
        var caption = typeof nameAttribute === 'string' ? nameAttribute : '';

        writeAttribute(image, 'src', source);
        /* An image carrying a data: URI and no alt text is announced by its
         * source. Authored alt text is never overwritten; a missing one is
         * supplied from the scenario name and removed again on close. */
        if (!hasAttribute(image, 'alt')) {
            writeAttribute(image, 'alt', caption);
            overlayOwnsImageAlt = true;
        } else {
            overlayOwnsImageAlt = false;
        }

        var captionElement = queryOne(overlay, SEL_LIGHTBOX_CAPTION);
        if (captionElement) {
            captionElement.textContent = caption;
        }

        /* Remembered before focus moves, so it is the element the reader was
         * actually on - normally the trigger itself. */
        focusBeforeOverlay = isElement(doc.activeElement) ? doc.activeElement : null;
        openOverlay = overlay;

        setClass(overlay, CLASS_LIGHTBOX_OPEN, true);
        if (!hasAttribute(overlay, 'role')) {
            writeAttribute(overlay, 'role', 'dialog');
        }
        if (!hasAttribute(overlay, ATTR_ARIA_MODAL)) {
            writeAttribute(overlay, ATTR_ARIA_MODAL, 'true');
        }
        writeAttribute(overlay, ATTR_ARIA_HIDDEN, 'false');

        var closeControl = queryOne(overlay, SEL_LIGHTBOX_CLOSE);
        var focusTarget = closeControl || overlay;
        if (focusTarget === overlay && !hasAttribute(overlay, 'tabindex')) {
            writeAttribute(overlay, 'tabindex', '-1');
        }
        safeFocus(focusTarget);
    }

    function closeLightbox() {
        var overlay = openOverlay;
        if (!overlay) {
            return;
        }

        setClass(overlay, CLASS_LIGHTBOX_OPEN, false);
        writeAttribute(overlay, ATTR_ARIA_HIDDEN, 'true');

        var image = queryOne(overlay, SEL_LIGHTBOX_IMAGE);
        if (image) {
            /* The attribute is REMOVED rather than emptied: an empty src is
             * resolved against the page's own address by some engines, which
             * would turn closing the overlay into a request for the document
             * itself. */
            image.removeAttribute('src');
            if (overlayOwnsImageAlt) {
                image.removeAttribute('alt');
            }
        }

        var captionElement = queryOne(overlay, SEL_LIGHTBOX_CAPTION);
        if (captionElement) {
            captionElement.textContent = '';
        }

        var restoreTo = focusBeforeOverlay;
        openOverlay = null;
        focusBeforeOverlay = null;
        overlayOwnsImageAlt = false;

        /* Only if it is still part of the document. */
        if (isConnected(restoreTo)) {
            safeFocus(restoreTo);
        }
    }

    /* A backdrop dismissal is a click on the explicit backdrop element itself,
     * or on the overlay container itself. Testing the target rather than its
     * ancestors is deliberate: it means a click that lands on the image, or on
     * anything nested inside the backdrop, never closes the overlay. */
    function isBackdropDismissal(target, overlay) {
        return target === overlay || matchesSelector(target, SEL_LIGHTBOX_BACKDROP);
    }

    /* -------------------------------------------------------------------
     * The initial ARIA sync
     *
     * Describes the page as authored and CHANGES NO CLASS. With nothing
     * collapsed and nothing filtered - the authored default - it writes
     * aria-expanded="true", aria-hidden="false" and aria-pressed="false",
     * which is exactly what the markup already means. Where the templates
     * chose to author a region collapsed, the class is read, honoured and
     * left alone.
     * ------------------------------------------------------------------- */
    function syncAria() {
        each(queryAll(doc, SEL_DETAIL), function (region) {
            writeAttribute(
                region,
                ATTR_ARIA_HIDDEN,
                hasClass(region, CLASS_COLLAPSED) ? 'true' : 'false'
            );
        });
        syncToggleControls(doc);
        syncFilterControls(doc);
    }

    /* -------------------------------------------------------------------
     * Event routing
     *
     * Two delegated listeners on the document, bound once. The document
     * exists as soon as this text is evaluated, so neither listener has to
     * wait for the DOM; delegation also means markup that arrives later needs
     * no wiring, and that re-evaluating this text cannot double-bind anything
     * to an individual element.
     *
     * Note that no default action is ever suppressed. Hook controls are
     * expected to be non-navigating buttons, and a handler that called
     * preventDefault would also swallow a genuine link inside a row.
     * ------------------------------------------------------------------- */

    function routeClick(event) {
        var target = event.target;
        if (!target) {
            return;
        }

        /* 1. A close control, wherever it sits. */
        if (closestMatch(target, SEL_LIGHTBOX_CLOSE)) {
            closeLightbox();
            return;
        }

        /* 2. Any other click that lands inside an overlay. Only a backdrop
         * dismissal acts; nothing inside an overlay is a report hook, so the
         * click stops here either way. */
        var overlay = closestMatch(target, SEL_LIGHTBOX);
        if (overlay) {
            if (overlay === openOverlay && isBackdropDismissal(target, overlay)) {
                closeLightbox();
            }
            return;
        }

        /* 3. A screenshot trigger. */
        var screenshotTrigger = closestMatch(target, SEL_SCREENSHOT);
        if (screenshotTrigger) {
            openLightbox(screenshotTrigger);
            return;
        }

        /* 4. A status filter control. */
        var filterControl = closestMatch(target, SEL_FILTER);
        if (filterControl) {
            handleFilterClick(filterControl);
            return;
        }

        /* 5. An expand-all or collapse-all control, before the single toggle,
         * so a control carrying both attributes acts on the whole scope. */
        var toggleAllControl = closestMatch(target, SEL_TOGGLE_ALL);
        if (toggleAllControl) {
            handleToggleAllClick(toggleAllControl);
            return;
        }

        /* 6. A single detail toggle. */
        var toggleControl = closestMatch(target, SEL_TOGGLE);
        if (toggleControl) {
            handleToggleClick(toggleControl);
        }
    }

    /* Escape closes the overlay, and only while it is open. No other key is
     * claimed, so the page keeps every native shortcut a reader expects. */
    function routeKeyDown(event) {
        if (!openOverlay) {
            return;
        }
        var key = event.key;
        if (key === 'Escape' || key === 'Esc') {
            closeLightbox();
        }
    }

    /* Nothing may escape a handler. A presentation fault here can only ever
     * degrade interactivity: it cannot alter a result, a count or a status, it
     * cannot reach an endpoint, and it cannot influence an exit code. Keeping
     * it contained - and emitting no diagnostic output, which this file does
     * nowhere - is what makes that guarantee hold in every branch. */
    function guarded(handler) {
        return function (event) {
            try {
                handler(event);
            } catch (error) {
                void error;
            }
        };
    }

    /* Stable references, so a repeated init call cannot register a second
     * copy of the same listener: the host deduplicates an identical
     * type/listener/capture triple. */
    var clickListener = guarded(routeClick);
    var keyDownListener = guarded(routeKeyDown);
    var ariaSyncListener = guarded(syncAria);

    var listenersBound = false;

    function bindListeners() {
        if (listenersBound) {
            return;
        }
        listenersBound = true;
        doc.addEventListener('click', clickListener, false);
        doc.addEventListener('keydown', keyDownListener, false);
    }

    /* -------------------------------------------------------------------
     * Initialization and the single global
     * ------------------------------------------------------------------- */

    function init() {
        bindListeners();
        if (doc.readyState === 'loading') {
            doc.addEventListener('DOMContentLoaded', ariaSyncListener, { once: true });
        } else {
            ariaSyncListener();
        }
    }

    /* The only global this file creates, and the guard the top of this
     * function tested. Its surface is deliberately tiny: the version, so a
     * reader of a saved report can tell what produced its behaviour, and an
     * idempotent init for a consumer that injects markup after load. */
    var api = {
        version: VERSION,
        init: init
    };

    globalScope[GLOBAL_NAME] = typeof Object.freeze === 'function' ? Object.freeze(api) : api;

    init();
}());

