/**
 * Replaces Google Apps Script google.script.run with fetch() to the Python backend.
 * Uses /api/app-call (not /api/rpc) — many ad blockers block URLs containing "rpc".
 */
(function () {
  var API_URL = "/api/app-call";

  function parseResponse(res, text) {
    var body = {};
    if (text) {
      try {
        body = JSON.parse(text);
      } catch (parseErr) {
        body = { error: text.slice(0, 500) || "Invalid server response" };
      }
    }
    if (res.ok && body.ok) {
      return body.result;
    }
    var detail = body.detail;
    if (Array.isArray(detail)) {
      detail = detail
        .map(function (item) {
          return item && item.msg ? item.msg : String(item);
        })
        .join("; ");
    }
    var message =
      body.error || detail || res.statusText || "Request failed";
    throw new Error(message);
  }

  function appCall(method, args) {
    return fetch(API_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: String(method), args: args || [] }),
    }).then(function (res) {
      return res.text().then(function (text) {
        return parseResponse(res, text);
      });
    });
  }

  window.appCall = appCall;

  function createRunner() {
    var onSuccess = function () {};
    var onFailure = function () {};

    var chain = {
      withSuccessHandler: function (fn) {
        onSuccess = fn || onSuccess;
        return chain;
      },
      withFailureHandler: function (fn) {
        onFailure = fn || onFailure;
        return chain;
      },
    };

    return new Proxy(chain, {
      get: function (target, prop) {
        if (prop in target) {
          return target[prop];
        }
        return function () {
          var args = Array.prototype.slice.call(arguments);
          appCall(prop, args)
            .then(function (result) {
              onSuccess(result);
            })
            .catch(function (err) {
              onFailure(err);
            });
        };
      },
    });
  }

  window.google = window.google || {};
  window.google.script = window.google.script || {};
  Object.defineProperty(window.google.script, "run", {
    configurable: true,
    get: function () {
      return createRunner();
    },
  });
})();
