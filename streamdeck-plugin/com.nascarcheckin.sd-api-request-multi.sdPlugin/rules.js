/* Matching logic for API Request Multi. Pure functions, no Stream Deck dependencies. */
var Rules = (function () {
    var FALSY = ['false', '0', '', 'undefined', 'null'];

    function norm(v) {
        return String(v).trim().toLowerCase();
    }

    function isTruthy(v) {
        return FALSY.indexOf(norm(v)) === -1;
    }

    function alternatives(ruleValue) {
        return String(ruleValue).split(',').map(norm).filter(function (s) { return s !== ''; });
    }

    /**
     * Does one rule match?
     *  - blank rule value          -> the field at the JSON path is "truthy"
     *  - JSON path set             -> field value equals the rule value (case-insensitive;
     *                                 "a, b" means a OR b)
     *  - JSON path blank (useBody) -> the response body contains the rule value
     */
    function matches(ruleValue, actual, useBody, body) {
        var rv = ruleValue === undefined || ruleValue === null ? '' : String(ruleValue);
        if (rv.trim() === '') {
            return !useBody && isTruthy(actual);
        }
        var alts = alternatives(rv);
        if (useBody) {
            var b = String(body || '').toLowerCase();
            return alts.some(function (a) { return b.indexOf(a) !== -1; });
        }
        var act = norm(actual);
        return alts.some(function (a) { return a === act; });
    }

    /** Returns {index, path}; index -1 means the default image. path may be undefined. */
    function choose(rules, defaultImage, actual, useBody, body) {
        var list = Array.isArray(rules) ? rules : [];
        for (var i = 0; i < list.length; i++) {
            var r = list[i];
            if (!r || !r.image) continue;
            if (matches(r.value, actual, useBody, body)) {
                return { index: i, path: r.image };
            }
        }
        return { index: -1, path: defaultImage || undefined };
    }

    return { matches: matches, choose: choose, isTruthy: isTruthy };
})();

if (typeof module !== 'undefined' && module.exports) {
    module.exports = Rules;
}
