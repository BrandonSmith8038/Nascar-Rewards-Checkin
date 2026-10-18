/* global $SD, Rules */
/* Derived from sd-api-request by Mike Beattie (Apache-2.0). See NOTICE. */
const PLUGIN_UUID = 'com.nascarcheckin.sd-api-request-multi';

$SD.on('connected', conn => connected(conn));

function connected(jsn) {
    debugLog('Connected Plugin:', jsn);

    $SD.on(`${PLUGIN_UUID}.didReceiveSettings`, jsonObj =>
        action.onDidReceiveSettings(jsonObj)
    );
    $SD.on(`${PLUGIN_UUID}.willAppear`, jsonObj =>
        action.onWillAppear(jsonObj)
    );
    $SD.on(`${PLUGIN_UUID}.willDisappear`, jsonObj =>
        action.onWillDisappear(jsonObj)
    );
    $SD.on(`${PLUGIN_UUID}.keyUp`, jsonObj =>
        action.onKeyUp(jsonObj)
    );
}

var action = {
    type: PLUGIN_UUID,
    cache: {},

    onDidReceiveSettings: function(jsn) {
        log('onDidReceiveSettings(): ', jsn);

        const settings = jsn.payload.settings;
        const api_request = this.cache[jsn.context];

        if(!settings || !api_request) return;

        api_request.updateSettings(settings);
        this.cache[jsn.context] = api_request;
    },

    onWillAppear: function(jsn) {
        log('onWillAppear(): ', jsn);

        if (!jsn.payload || !jsn.payload.hasOwnProperty('settings')) return;

        const api_request = new APIRequest(jsn);
        this.cache[jsn.context] = api_request;
    },

    onWillDisappear: function(jsn) {
        log('onWillDisappear(): ', jsn);

        let api_request = this.cache[jsn.context];

        if (api_request) {
            api_request.destroy();
            delete this.cache[jsn.context];
        }
    },

    onKeyUp: function(jsn) {
        log('onKeyUp(): ', jsn);

        const api_request = this.cache[jsn.context];

        if(!api_request)
            this.onWillAppear(jsn);
        else
            api_request.sendRequest();
    }
};


function APIRequest(jsonObj) {
    var settings = jsonObj.payload.settings,
        context = jsonObj.context,
        poll_timer = 0,
        current_image = null,
        poll_errors = 0;

    function startPeriodicPoll() {
        if(poll_timer !== 0) {
            window.clearInterval(poll_timer);
            poll_timer = 0;
        }

        const frequency = Number(settings.poll_status_frequency) || 15;

        poll_timer = setInterval(function() {
            sendRequest(true);
        }, 1000 * frequency);
    }

    function pollConfigured() {
        return Boolean(settings.response_parse) && Boolean(settings.poll_status) && Boolean(settings.poll_status_url);
    }

    function sendRequest(do_status_poll = false) {
        if (do_status_poll) {
            if (!pollConfigured()) return;
        } else if (!settings.request_url) {
            // No request URL set: a key press just refreshes the status (or alerts if nothing is configured)
            if (pollConfigured()) {
                sendRequest(true);
            } else {
                $SD.api.showAlert(context);
            }
            return;
        }

        let url    = do_status_poll ? settings.poll_status_url : settings.request_url;
        let method = (do_status_poll ? settings.poll_status_method : settings.request_method) || 'GET';

        const opts = {
            cache: 'no-cache',
            headers: constructHeaders(),
            method: method,
            body: ['GET', 'HEAD'].includes(method)
                                    ? undefined
                                    : settings.request_body,
        };

        log('sendRequest(): URL:', url, 'ARGS:', opts);

        fetch(url, opts)
            .then((resp) => checkResponseStatus(resp))
            .then((resp) => updateImage(resp, do_status_poll))
            .then((resp) => showSuccess(resp, do_status_poll))
            .catch(err => {
                log(err);
                if (do_status_poll) {
                    onPollError();
                } else {
                    $SD.api.showAlert(context);
                }
            }
        );

        if (!do_status_poll)
            startPeriodicPoll();
    }

    // Two failed polls in a row (server down/unreachable): show the default image if there is one,
    // otherwise flash the alert triangle.
    function onPollError() {
        poll_errors += 1;
        if (poll_errors < 2) return;
        if (settings.image_default) {
            showImage(settings.image_default);
        } else {
            $SD.api.showAlert(context);
        }
    }

    function constructHeaders() {
        let default_headers = settings.request_content_type
                                ? { 'Content-Type': settings.request_content_type }
                                : {};
        let input_headers = {};

        if (settings.request_headers) {
            settings.request_headers.split(/\n/).forEach(h => {
                if (h.includes(':')) {
                    const [name, value] = h.split(/: *(.*)/).map(s => {
                        return s.trim();
                    });

                    if (name) {
                        input_headers[name] = value;
                    }
                }
            });
        }

        return {
            ...default_headers,
            ...input_headers
        }
    }

    async function checkResponseStatus(resp) {
        if (!resp) {
            throw new Error();
        }
        if (!resp.ok) {
            throw new Error(`${resp.status}: ${resp.statusText}\n${await resp.text()}`);
        }
        return resp;
    }

    function showImage(path) {
        if (!path || path === current_image) return;
        current_image = path;
        log('showImage(): FILE:', path);
        Utils.loadImage(path, img => $SD.api.setImage(context, img));
    }

    async function updateImage(resp, do_status_poll) {
        if (!settings.response_parse) return resp;

        const use_poll_field = do_status_poll && settings.poll_status && settings.poll_status_parse && settings.poll_status_parse_field;
        const field = String((use_poll_field ? settings.poll_status_parse_field : settings.response_parse_field) || '').trim();
        const use_body = field === '';

        let json, body, actual;
        if (use_body) {
            body = await resp.text();
        } else {
            json = await resp.json();
            actual = Utils.getProperty(json, field);
        }

        if (do_status_poll) poll_errors = 0;

        const pick = Rules.choose(settings.rules, settings.image_default, actual, use_body, body);
        log('updateImage(): value:', actual, 'rule:', pick.index, 'FILE:', pick.path);
        showImage(pick.path);

        return resp;
    }

    function showSuccess(resp, do_status_poll) {
        if (!do_status_poll && Boolean(settings.enable_success_indicator))
            $SD.api.showOk(context)
        return resp;
    }

    function updateSettings(new_settings) {
        settings = new_settings;
        current_image = null;      // force the image to be re-applied with the new rules
        poll_errors = 0;
        startPeriodicPoll();
        sendRequest(true);
    }

    function destroy() {
        if(poll_timer !== 0) {
            window.clearInterval(poll_timer);
            poll_timer = 0;
        }
    }

    startPeriodicPoll();
    sendRequest(true);

    return {
        sendRequest: sendRequest,
        updateSettings: updateSettings,
        destroy: destroy
    };
}

function log(...msg) {
    console.log(`[${new Date().toLocaleTimeString('UTC', {hourCycle: 'h23'})}]`, ...msg);
}
