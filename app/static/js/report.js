/*
 * Interactive behaviour for the generated test reports.
 * -----------------------------------------------------
 *
 * The one hand-written browser script in this project. Three consumers embed
 * or serve this exact text, unchanged, unbundled and untranspiled:
 *
 *   1. The self-contained-artifact writer inlines it into the single report
 *      page it produces.
 *   2. The report-tree writer emits it beside the pages of the tree it
 *      produces, under that tree's own script name.
 *   3. The viewer application serves it from its package-relative static
 *      folder, which the view templates reference through the framework's
 *      own static-asset URL builder.
 *
 * No path, name or scheme belonging to any of those three is written
 * anywhere in this file. Two of the three copy this text verbatim into a
 * page that must carry no reference to anything outside itself, so a
 * reference written even in a comment would travel into the artifact and
 * break exactly the property the artifact is checked for.
 *
 * Four consequences shape every line below.
 *
 *   - It is inlined into an HTML page. It therefore contains no sequence that
 *     could terminate or corrupt the surrounding markup, and no module syntax
 *     of any kind: it is always evaluated as a classic script, never as a
 *     module, so it neither imports nor exports anything.
 *   - The reports are opened straight from a continuous-integration
 *     workspace, off the local filesystem rather than from a server.
 *     Nothing here assumes a server, an origin, a base URL or an absolute
 *     path, and no code path can issue a network request: the lightbox only
 *     ever accepts a source that is already an inline data URI (see below).
 *   - It may be evaluated before or after DOMContentLoaded, and possibly twice
 *     on one page. It therefore binds delegated listeners once, guards
 *     re-evaluation with a single namespaced global, and defers only its
 *     initial ARIA sync.
 *   - It has no dependency whatsoever: no library, no polyfill, no build step,
 *     no vendored global. The third-party libraries that ship beside the
 *     emitted report tree are that generator's own output and are
 *     deliberately never referenced here, because neither the self-contained
 *     artifact nor the viewer's pages load them.
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
 * This script only ever toggles state. The project's own stylesheet owns how
 * that state renders, and the templates own the markup - the artifact
 * templates, the report-tree templates and the viewer's views all share the
 * same three partials (status badge, step row, screenshot lightbox), so one
 * vocabulary has to serve all three. Publishing the whole contract here is
 * what lets the stylesheet and the partials converge on it.
 *
 * THERE IS EXACTLY ONE STATE VOCABULARY, and it is the one below. Three
 * runtime classes, written only here and read only by the stylesheet;
 * thirteen authored data hooks, written only by the templates and read only
 * here; and aria-controls as the single way a control names the region it
 * operates. No parallel family of state attributes exists, and none may be
 * added: a second vocabulary is a second contract, and the one that no
 * template produces is the one that silently stops working.
 *
 * Selection is by data attribute only. The generator classes carried by the
 * emitted report-tree pages (passed, step, element, collapsable-control,
 * chevron, panel and the rest) are never selected on and never modified:
 * they are that generator's presentation vocabulary, owned by the stylesheet
 * that ships beside those pages, and this script has no business in it.
 * Result-detail collapse on those pages is NOT that widget's - the templates
 * author every region expanded and hand it to the hooks below, which is what
 * makes the pages complete with no scripting and complete in print - so
 * there is nothing here to reconcile with a second collapse mechanism.
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
 *       type="button">. Toggles <status> in the shown-set for its scope, and
 *       carries aria-pressed mirroring that membership. "all" is the one
 *       reserved value and clears the set: it carries aria-pressed too, and
 *       is pressed exactly while the shown-set is EMPTY, the state in which
 *       every row is shown. A value that is empty or whitespace-only is
 *       treated as "all", because no element can carry an empty status and
 *       the alternative would hide everything.
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
 *       A control that toggles one detail region. The region it operates is
 *       named by aria-controls, which is the SOLE target reference: there is
 *       no second target attribute, because two ways to name one region are
 *       two things to keep in agreement and the accessible one has to be
 *       present regardless. A control whose aria-controls resolves to
 *       nothing falls back to the first detail region inside its own row.
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
 *       used instead. The shared partial emits a real <button type="button">
 *       around the thumbnail, so activation, focus and the keyboard come
 *       from the platform. A trigger that is NOT a native button is still
 *       supported: it is given a tabindex and a button role if it carries
 *       neither, and Enter and Space activate it here.
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
 * BACKDROP DISMISSAL is a click whose target IS the overlay container
 * itself. There is no separate backdrop element and no hook for one: an
 * empty flex child has no size, so it could never be clicked, and the
 * container already covers the whole viewport.
 *
 * THE ONLY IMAGE SOURCE THIS SCRIPT WILL USE is an inline PNG data URI:
 * a literal "data:image/png;base64," prefix followed by a canonical base64
 * payload that begins with the base64 encoding of the PNG signature. A
 * value of any other media type, any other encoding, or a malformed
 * payload is rejected and the overlay does not open. That is the same test
 * the screenshot partial applies before it emits a thumbnail at all, so the
 * two cannot disagree, and it is what keeps a result-controlled value from
 * becoming an active document inside the page.
 *
 * STATE CLASSES - exactly three, and no fourth is permitted
 * ---------------------------------------------------------
 *   report-is-collapsed        on [data-report-detail]      detail hidden
 *   report-is-filtered-out     on [data-report-filterable]  row hidden by the
 *                                                           status filter
 *   report-lightbox-is-open    on [data-report-lightbox]     overlay visible
 *
 * Active-filter styling is expressed through aria-pressed="true|false" on a
 * filter control, which the stylesheet can select on. That keeps the class
 * set at three and is also the accessible form.
 *
 * The reserved "all" control is pressed exactly when NO status is selected,
 * which is the state in which every row is shown. It is a toggle like the
 * others and reports its state truthfully rather than being permanently
 * unpressed while it is the control describing what the reader sees.
 *
 * ARIA MIRRORS
 * ------------
 * State is mirrored into ARIA rather than into inline style or the hidden
 * attribute, because the stylesheet owns rendering:
 *   aria-expanded on the toggle control, aria-hidden on the detail region,
 *   aria-pressed on every filter control, the reserved "all" control
 *   included, and on the open overlay
 *   role="dialog", aria-modal="true" and aria-hidden="false" (role and
 *   aria-modal only when the markup has not already set them).
 *
 * MODAL FOCUS - the one place a default action is suppressed
 * ---------------------------------------------------------
 * An overlay that claims aria-modal="true" has to behave like one, so while
 * it is open:
 *   - focus moves into it on open, to its close control;
 *   - Tab and Shift+Tab are contained inside it, wrapping at both ends, and
 *     those two keystrokes are the ONLY default actions this file ever
 *     prevents - a modal that lets Tab walk into the obscured report behind
 *     it is a modal in name only;
 *   - Escape dismisses it;
 *   - on close, focus returns to THE TRIGGER THAT OPENED IT, remembered as
 *     an element rather than read back from the document, because a pointer
 *     activation commonly leaves the active element somewhere else entirely.
 *
 * REQUIREMENTS ON THE MARKUP
 * --------------------------
 *   - The authored default is expanded and unfiltered. Initialization sets
 *     ARIA attributes to match that authored state and changes no class, so a
 *     reader with scripting disabled loses interactivity but never content.
 *     The stylesheet must render all three states, and must render their
 *     absence as the fully expanded, unfiltered page.
 *   - Hook controls must be non-navigating - <button type="button"> - because
 *     no click handled here suppresses an authored default action. That is
 *     deliberate: a hook that swallowed default actions would also swallow a
 *     genuine link inside a row. A real button also carries focusability and
 *     Enter/Space activation from the platform, which is why every control
 *     the shared partials emit is one.
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
    var ATTR_TOGGLE_ALL = 'data-report-toggle-all';
    var ATTR_DETAIL = 'data-report-detail';
    var ATTR_SCREENSHOT = 'data-report-screenshot';
    var ATTR_SCREENSHOT_NAME = 'data-report-screenshot-name';
    var ATTR_LIGHTBOX = 'data-report-lightbox';
    var ATTR_LIGHTBOX_IMAGE = 'data-report-lightbox-image';
    var ATTR_LIGHTBOX_CAPTION = 'data-report-lightbox-caption';
    var ATTR_LIGHTBOX_CLOSE = 'data-report-lightbox-close';

    var ATTR_ARIA_CONTROLS = 'aria-controls';
    var ATTR_ARIA_DESCRIBEDBY = 'aria-describedby';
    var ATTR_ARIA_EXPANDED = 'aria-expanded';
    var ATTR_ARIA_HIDDEN = 'aria-hidden';
    var ATTR_ARIA_MODAL = 'aria-modal';
    var ATTR_ARIA_PRESSED = 'aria-pressed';

    /* The reserved filter value. */
    var FILTER_ALL = 'all';

    /* The only image source this script will ever hand to an img element,
     * spelled out in full: media type, encoding and payload shape.
     *
     * PNG_SIGNATURE_BYTES is the file signature from PNG's specification, all
     * eight bytes of it. The payload is decoded and its first eight bytes are
     * compared against these, rather than its first characters being compared
     * against a base64 prefix: a prefix of eight characters pins only the
     * first SIX bytes, because base64 maps three bytes onto four characters,
     * and a payload agreeing in six bytes is base64 of something that is not
     * a PNG. Comparing decoded bytes has no such edge and needs no arithmetic
     * to justify.
     *
     * MAX_BASE64_CHARS mirrors the backend authority's decoded-size bound at
     * the encoded length that implies it - 32 MiB of image, four characters
     * per three bytes - and is checked before the decode, so an oversized
     * payload is never expanded in the page. */
    var DATA_PNG_PREFIX = 'data:image/png;base64,';
    var PNG_SIGNATURE_BYTES = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
    var BASE64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    var MAX_BASE64_CHARS = 44739244;

    /* Selectors are derived from the attribute names above so the two can
     * never drift apart. An attribute selector matches an exact attribute
     * name, so [data-report-toggle] does not also match a control that
     * carries data-report-toggle-all. */
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

    /* Everything inside the overlay that can hold focus while it is open.
     * Used only to contain Tab, so it is the ordinary interactive set plus
     * anything the markup has explicitly made focusable; a negative tabindex
     * is excluded because it is reachable by script and not by Tab. */
    var SEL_FOCUSABLE = 'a[href], area[href], button:not([disabled]), ' +
        'input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
        'textarea:not([disabled]), iframe, object, embed, summary, ' +
        'audio[controls], video[controls], [contenteditable="true"], ' +
        '[tabindex]:not([tabindex^="-"])';

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

    /* A status control is pressed exactly when its status is in the show-set
     * of its own scope, which is what keeps sibling roots independent.
     *
     * The reserved "all" control - and an empty value, which means the same
     * thing - is pressed exactly when the show-set is EMPTY, because an empty
     * set is the state in which everything is shown and "all" is therefore
     * the control that describes what the reader is looking at. Reporting it
     * permanently unpressed would announce the unfiltered page as having no
     * active filter control at all, and would leave the stylesheet's pressed
     * treatment unreachable on the one control that is active by default. */
    function isFilterPressed(control) {
        var value = normalize(readAttribute(control, ATTR_FILTER));
        var shown = shownStatusesFor(resolveScope(control), false);
        if (value === '' || value === FILTER_ALL) {
            return !shown || shown.length === 0;
        }
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

    /* Resolution order, fixed and two-step: aria-controls, then the first
     * detail region inside the control's own row. aria-controls is the ONE
     * target reference - the accessible attribute doubles as the functional
     * one, so a control can never point one way for a reader and another way
     * for the script. The row is the closest filterable element, falling back
     * to the control's parent, which is what lets a row-local toggle work
     * without an id at all. Nothing resolving is a silent no-op at the call
     * site. */
    function resolveDetailRegion(control) {
        var region = firstElementById(readAttribute(control, ATTR_ARIA_CONTROLS));
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
     * That is why the source is validated before use, and validated to the
     * exact shape the contract permits rather than to a family of shapes:
     * the literal prefix "data:image/png;base64,", a payload drawn only from
     * the base64 alphabet with correct padding, and the base64 encoding of
     * the PNG signature at its head. Everything else is rejected and the
     * overlay does not open - an absolute URL, a protocol-relative one, a
     * relative path, a different media type, an unencoded or percent-encoded
     * payload, and in particular a scalable-vector payload, which is an
     * active document able to carry script and is never a screenshot this
     * project produces.
     *
     * There is consequently no code path in this file that can cause the
     * browser to request anything, and none that can turn a result-supplied
     * value into anything but a raster image, which is what makes the reports
     * safe to read straight from a workspace with no network at all.
     *
     * There is deliberately no capture control and no enable switch here: the
     * documentation's claim of screenshots for passing tests describes an
     * intent the implementation never had.
     * ------------------------------------------------------------------- */

    var openOverlay = null;
    /* The element that opened the overlay, remembered as an element. Focus
     * returns HERE on close - not to whatever the document reported as
     * active at the moment of opening, which after a pointer activation is
     * routinely the body or an ancestor rather than the trigger. */
    var overlayTrigger = null;
    /* Whether this script, rather than the markup, supplied the overlay
     * image's alt text, and the authored value to put back on close - null
     * meaning the attribute was absent and must be removed again. */
    var overlayOwnsImageAlt = false;
    var overlayAuthoredAlt = null;
    /* Whether this script, rather than the markup, associated the caption
     * with the dialog. */
    var overlayOwnsDescription = false;

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

    /* Canonical base64: the standard alphabet only, a length that is a
     * multiple of four, and padding that appears only as the last one or two
     * characters. Written as a character scan rather than a regular
     * expression so the accepted set is visible in the source and cannot be
     * widened by an escape read two ways. */
    function isCanonicalBase64(payload) {
        if (typeof payload !== 'string' || payload.length === 0 || payload.length % 4 !== 0) {
            return false;
        }
        var padding = 0;
        for (var index = 0; index < payload.length; index += 1) {
            var character = payload.charAt(index);
            if (character === '=') {
                /* Padding is only ever the final one or two characters. */
                if (index < payload.length - 2) {
                    return false;
                }
                padding += 1;
            } else {
                /* A payload character after a padding character is invalid. */
                if (padding > 0 || BASE64_ALPHABET.indexOf(character) === -1) {
                    return false;
                }
            }
        }
        return true;
    }

    /* Whether the platform already activates this element from the keyboard,
     * in which case the key handling below must stay out of its way or every
     * activation would be counted twice. */
    function isNativelyActivated(element) {
        if (!isElement(element)) {
            return false;
        }
        var name = (element.localName || '').toLowerCase();
        if (name === 'button' || name === 'summary') {
            return true;
        }
        if (name === 'a' || name === 'area') {
            return hasAttribute(element, 'href');
        }
        return name === 'input';
    }

    /* Makes a trigger the markup did not build as a button operable anyway:
     * reachable by Tab and announced as a control. Called once per trigger
     * from the initial sync, and it writes nothing where the markup already
     * says something - so the shared partial's real <button>, and any caller
     * that supplied its own role or tabindex, are left exactly as authored. */
    function promoteScreenshotTrigger(trigger) {
        if (!isElement(trigger) || isNativelyActivated(trigger)) {
            return;
        }
        if (!hasAttribute(trigger, 'tabindex')) {
            writeAttribute(trigger, 'tabindex', '0');
        }
        if (!hasAttribute(trigger, 'role')) {
            writeAttribute(trigger, 'role', 'button');
        }
    }

    /* Decodes a base64 payload and answers its bytes only if it is the
     * canonical encoding of a PNG. Four things are established, and each
     * exists because a weaker test admits something concrete:
     *
     *  - length and alphabet, by isCanonicalBase64 above, BEFORE decoding.
     *    atob is lenient about whitespace and about some malformed padding,
     *    so the scan is what fixes the accepted set rather than atob;
     *  - decodability, by atob itself. It throws a DOMException on what it
     *    will not take, and a throw here is a rejection rather than an error -
     *    a malformed attachment must not break the page it appears on;
     *  - canonicality, by re-encoding the decoded bytes with btoa and
     *    requiring the original payload back. The alphabet scan cannot see
     *    non-zero unused pad bits: "iVBORw0KAB==" passes it, yet its bytes
     *    canonically encode as "iVBORw0KAA==", so a reader would have two
     *    spellings of one payload;
     *  - the complete eight-byte PNG signature, on the decoded bytes.
     *
     * atob returns a binary string, one character per byte with a code point
     * of 0-255, so charCodeAt IS the byte and no typed array is needed -
     * which keeps this working in a document opened straight off disk, with
     * no fetch, no module loader and nothing but the DOM. */
    function decodePngPayload(payload) {
        if (typeof payload !== 'string' || payload.length > MAX_BASE64_CHARS) {
            return null;
        }
        if (!isCanonicalBase64(payload)) {
            return null;
        }
        var bytes;
        var reencoded;
        try {
            bytes = atob(payload);
            reencoded = btoa(bytes);
        } catch (error) {
            /* Anything atob or btoa refuses is a rejected payload, never an
             * exception the caller has to handle. */
            return null;
        }
        if (reencoded !== payload) {
            return null;
        }
        if (bytes.length < PNG_SIGNATURE_BYTES.length) {
            return null;
        }
        for (var index = 0; index < PNG_SIGNATURE_BYTES.length; index += 1) {
            if (bytes.charCodeAt(index) !== PNG_SIGNATURE_BYTES[index]) {
                return null;
            }
        }
        return bytes;
    }

    /* The single gate on every image source this script assigns. The
     * media-type prefix is matched case-insensitively, because a media type
     * is case-insensitive; the payload is not, because base64 is. */
    function isInlinePngSource(value) {
        if (typeof value !== 'string') {
            return false;
        }
        var trimmed = value.trim();
        if (trimmed.slice(0, DATA_PNG_PREFIX.length).toLowerCase() !== DATA_PNG_PREFIX) {
            return false;
        }
        return decodePngPayload(trimmed.slice(DATA_PNG_PREFIX.length)) !== null;
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
        if (!isInlinePngSource(source)) {
            return;
        }

        var nameAttribute = readAttribute(trigger, ATTR_SCREENSHOT_NAME);
        /* Never render the string "null" or "undefined" as visible text. */
        var caption = typeof nameAttribute === 'string' ? nameAttribute : '';

        writeAttribute(image, 'src', source);
        /* The enlarged screenshot is failure evidence, so it is never left
         * announced by its own data URI. The overlay ships with an empty alt -
         * correct for an image with no source, and the only form that is
         * valid markup - so BLANK counts as unset here just as a missing
         * attribute does: either is replaced with the scenario name and
         * restored to the authored empty string on close. Alt text the markup
         * genuinely authored is still never overwritten. */
        var authoredAlt = readAttribute(image, 'alt');
        var altIsBlank = typeof authoredAlt !== 'string' || authoredAlt.trim() === '';
        if (altIsBlank && caption !== '') {
            overlayAuthoredAlt = typeof authoredAlt === 'string' ? authoredAlt : null;
            writeAttribute(image, 'alt', caption);
            overlayOwnsImageAlt = true;
        } else {
            overlayOwnsImageAlt = false;
            overlayAuthoredAlt = null;
        }

        /* The caption is the image's description as well as its visible text,
         * so it is associated with the dialog rather than only sitting near
         * it. The association is made only when the caption element carries
         * an id and the markup has not already described the overlay. */
        var captionElement = queryOne(overlay, SEL_LIGHTBOX_CAPTION);
        if (captionElement) {
            captionElement.textContent = caption;
            var captionId = readAttribute(captionElement, 'id');
            if (caption !== '' && typeof captionId === 'string' && captionId !== '' &&
                    !hasAttribute(overlay, ATTR_ARIA_DESCRIBEDBY)) {
                writeAttribute(overlay, ATTR_ARIA_DESCRIBEDBY, captionId);
                overlayOwnsDescription = true;
            }
        }

        /* The trigger itself, remembered as an element. */
        overlayTrigger = isElement(trigger) ? trigger : null;
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
        /* The mirror is REMOVED rather than set to "true". Once the class is
         * gone the overlay is display:none, so it is already outside the
         * accessibility tree and a "true" here would add nothing; removing the
         * attribute also restores exactly the state the template authored,
         * which emits no visibility attribute at all.
         *
         * Writing "true" at this point is what made Chrome report "Blocked
         * aria-hidden on an element because its descendant retained focus":
         * the close control inside the overlay still holds focus here, and the
         * browser refuses to hide a focused element's ancestor from assistive
         * technology. Removing an attribute can never trip that check, so this
         * holds on every close path - the close control, the backdrop and the
         * Escape key alike - rather than only where focus happens to have
         * somewhere to go back to. */
        if (typeof overlay.removeAttribute === 'function') {
            overlay.removeAttribute(ATTR_ARIA_HIDDEN);
        }

        var image = queryOne(overlay, SEL_LIGHTBOX_IMAGE);
        if (image) {
            /* The attribute is REMOVED rather than emptied: an empty src is
             * resolved against the page's own address by some engines, which
             * would turn closing the overlay into a request for the document
             * itself. */
            image.removeAttribute('src');
            if (overlayOwnsImageAlt) {
                /* Exactly what the markup authored goes back: an empty string
                 * where it authored one, and no attribute at all where it
                 * authored none. */
                if (overlayAuthoredAlt === null) {
                    image.removeAttribute('alt');
                } else {
                    writeAttribute(image, 'alt', overlayAuthoredAlt);
                }
            }
        }

        var captionElement = queryOne(overlay, SEL_LIGHTBOX_CAPTION);
        if (captionElement) {
            captionElement.textContent = '';
        }
        if (overlayOwnsDescription && typeof overlay.removeAttribute === 'function') {
            overlay.removeAttribute(ATTR_ARIA_DESCRIBEDBY);
        }

        var restoreTo = overlayTrigger;
        openOverlay = null;
        overlayTrigger = null;
        overlayOwnsImageAlt = false;
        overlayAuthoredAlt = null;
        overlayOwnsDescription = false;

        /* Back to the control that opened the overlay, and only if it is still
         * part of the document. */
        if (isConnected(restoreTo)) {
            safeFocus(restoreTo);
        }
    }

    /* A backdrop dismissal is a click whose target IS the overlay container.
     * Testing the target rather than its ancestors is what makes a click on
     * the image, the caption or the close control something other than a
     * dismissal, and the container is the full-viewport layer itself, so it
     * needs no separate backdrop child to be clickable. */
    function isBackdropDismissal(target, overlay) {
        return target === overlay;
    }

    /* -------------------------------------------------------------------
     * Modal focus containment
     *
     * Only while an overlay is open, and only for Tab and Shift+Tab. The
     * candidate set is recomputed per keystroke rather than cached, because
     * the overlay's own contents are written on open.
     * ------------------------------------------------------------------- */

    function focusableWithin(container) {
        var candidates = queryAll(container, SEL_FOCUSABLE);
        var usable = [];
        each(candidates, function (candidate) {
            /* offsetParent is null for a display:none subtree, which an open
             * overlay's contents never are; the guard is for a control the
             * markup chose to hide inside one. */
            if (isElement(candidate) &&
                    (candidate.offsetWidth > 0 || candidate.offsetHeight > 0 ||
                        candidate === doc.activeElement)) {
                usable.push(candidate);
            }
        });
        return usable;
    }

    /* Returns true when the keystroke was handled and its default action must
     * be suppressed - the one case in this file where that happens. */
    function containFocus(event, overlay) {
        var focusable = focusableWithin(overlay);
        if (focusable.length === 0) {
            /* Nothing inside can hold focus, so the overlay itself does. */
            safeFocus(overlay);
            return true;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        var active = doc.activeElement;
        var inside = isElement(active) &&
            typeof overlay.contains === 'function' &&
            overlay.contains(active);

        if (!inside) {
            safeFocus(event.shiftKey ? last : first);
            return true;
        }
        if (event.shiftKey && active === first) {
            safeFocus(last);
            return true;
        }
        if (!event.shiftKey && active === last) {
            safeFocus(first);
            return true;
        }
        /* Focus is inside and not at an edge, so the platform's own Tab
         * order already keeps it inside: nothing to do and nothing to
         * suppress. */
        return false;
    }

    /* -------------------------------------------------------------------
     * The initial ARIA sync
     *
     * Describes the page as authored and CHANGES NO CLASS. With nothing
     * collapsed and nothing filtered - the authored default - it writes
     * aria-expanded="true" and aria-hidden="false", aria-pressed="false"
     * on each per-status filter control and aria-pressed="true" on the
     * reserved "all" control, which is exactly what the markup already
     * means. Where the templates chose to author a
     * region collapsed, the class is read, honoured and left alone.
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
        each(queryAll(doc, SEL_SCREENSHOT), promoteScreenshotTrigger);
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
     * NO CLICK EVER SUPPRESSES A DEFAULT ACTION. Hook controls are expected
     * to be non-navigating buttons, and a click handler that called
     * preventDefault would also swallow a genuine link inside a row.
     *
     * The keystroke handler suppresses a default action in exactly two
     * situations, both of them required rather than convenient: Tab while a
     * modal overlay is open, which is what containment means, and Enter or
     * Space on a screenshot trigger the markup did not build as a button,
     * where Space would otherwise scroll the page instead of opening the
     * image. Both are inside the overlay's or the trigger's own interaction
     * and neither can reach a link.
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

    /* Keys, in the only two situations this script claims one.
     *
     * While an overlay is open: Escape dismisses it, and Tab is contained
     * inside it. Nothing else is claimed, so the page keeps every native
     * shortcut a reader expects.
     *
     * While no overlay is open: Enter and Space activate a screenshot
     * trigger that is not a native button. A native button needs nothing
     * here - the platform already fires a click for both keys - and the
     * shared partial emits one, so this path exists for markup that does
     * not, and it is the reason such markup is operable at all. */
    function routeKeyDown(event) {
        var key = event.key;

        if (openOverlay) {
            if (key === 'Escape' || key === 'Esc') {
                closeLightbox();
                return;
            }
            if (key === 'Tab' && containFocus(event, openOverlay) &&
                    typeof event.preventDefault === 'function') {
                event.preventDefault();
            }
            return;
        }

        if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar') {
            return;
        }
        var trigger = closestMatch(event.target, SEL_SCREENSHOT);
        if (!trigger || isNativelyActivated(trigger)) {
            return;
        }
        /* Space would otherwise scroll the page, and Enter on a
         * non-interactive element does nothing to suppress. */
        if (typeof event.preventDefault === 'function') {
            event.preventDefault();
        }
        openLightbox(trigger);
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
