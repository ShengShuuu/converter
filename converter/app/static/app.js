// Page behavior for Order Converter. Loaded before the Alpine CSP build, which cannot
// evaluate inline expressions, so every Alpine component is registered here by name.
(function () {
  "use strict";

  function parseBbox(value) {
    if (!value) return null;
    var parts = value.split(",").map(Number);
    if (parts.length !== 4 || parts.some(function (n) { return !isFinite(n); })) return null;
    return { left: parts[0], top: parts[1], width: parts[2], height: parts[3] };
  }

  function cellLabel(cell) {
    var label = cell.querySelector(".field-cell__label");
    return label ? label.textContent.trim() : "this field";
  }

  function focusInput(input) {
    input.focus();
    if (typeof input.select === "function") input.select();
  }

  function cellInputs(root) {
    return Array.prototype.slice.call(root.querySelectorAll(".cell-input"));
  }

  function inputAfter(root, reference) {
    var inputs = cellInputs(root);
    for (var i = 0; i < inputs.length; i++) {
      var follows = reference.compareDocumentPosition(inputs[i]) & Node.DOCUMENT_POSITION_FOLLOWING;
      if (follows && !reference.contains(inputs[i])) return inputs[i];
    }
    return null;
  }

  // The custom-text input sits next to its "Filled with" select; shown only for the data-custom option.
  function syncCustomText(select, focusWhenShown) {
    var input = select.parentNode.querySelector(".custom-text");
    var option = select.options[select.selectedIndex];
    var isCustom = !!option && option.hasAttribute("data-custom");
    input.hidden = !isCustom;
    if (isCustom && focusWhenShown) input.focus();
  }

  function onFieldChange(event) {
    syncCustomText(event.target, true);
  }

  // Back/forward-cache restores a <select>'s value but not a hidden, autocomplete=off text input
  // next to it, so the visible "Custom" option can be left with no text box showing.
  function resyncCustomText(root) {
    Array.prototype.slice.call(root.querySelectorAll(".custom-text")).forEach(function (input) {
      syncCustomText(input.parentNode.querySelector("select"), false);
    });
  }
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) document.querySelectorAll(".mapping-table").forEach(resyncCustomText);
  });

  document.addEventListener("alpine:init", function () {
    window.Alpine.data("navMenu", function () {
      return {
        open: false,
        get expandedLabel() { return this.open ? "true" : "false"; },
        toggle: function () { this.open = !this.open; },
        close: function () { this.open = false; }
      };
    });

    window.Alpine.data("review", function () {
      // Kept outside reactive state: Alpine proxies would break DOM methods on these nodes.
      var lastFocusedInput = null;

      return {
        box: null,
        imageWidth: 0,
        zoomed: false,
        imageFailed: false,
        // v1: caption: "Click or tab into a field to highlight where it was read on the scan.",
        caption: "Click any box on the right to see where it is on the scan.",

        init: function () {
          var img = this.$refs.page;
          if (img && img.complete && img.naturalWidth) this.measure();
        },

        get pageWidth() {
          return Number(this.$refs.figure.dataset.pageWidth) || 0;
        },
        get scale() {
          return this.pageWidth && this.imageWidth ? this.imageWidth / this.pageWidth : 0;
        },
        get hasBox() {
          return this.box !== null && this.scale > 0;
        },
        get boxStyle() {
          if (!this.hasBox) return {};
          var s = this.scale;
          // Object form: Alpine applies it via CSSOM, which strict CSP allows (a style attribute string would be blocked).
          return {
            left: this.box.left * s + "px",
            top: this.box.top * s + "px",
            width: Math.max(this.box.width * s, 4) + "px",
            height: Math.max(this.box.height * s, 4) + "px",
          };
        },
        get zoomClass() {
          return this.zoomed ? "scan--zoomed" : "";
        },

        measure: function () {
          var img = this.$refs.page;
          this.imageWidth = img ? img.clientWidth : 0;
        },
        onImageError: function () {
          this.imageFailed = true;
          this.box = null;
        },
        toggleZoom: function () {
          this.zoomed = !this.zoomed;
          this.$nextTick(function () {
            this.measure();
            this.scrollBoxIntoView();
          }.bind(this));
        },

        onFocusIn: function (event) {
          var cell = event.target.closest ? event.target.closest(".field-cell") : null;
          if (!cell) return;
          if (event.target.classList.contains("cell-input")) lastFocusedInput = event.target;
          var bbox = parseBbox(cell.dataset.bbox);
          var label = cellLabel(cell);
          if (!bbox) {
            this.box = null;
            // v1: this.caption = "No position on the scan was recorded for " + label + ".";
            this.caption = "We can't show where " + label + " is on the scan.";
            return;
          }
          if (!this.pageWidth) {
            this.box = null;
            // v1: this.caption = "Highlighting isn't available: the scan size is unknown.";
            this.caption = "Highlighting isn't available for this scan.";
            return;
          }
          this.box = bbox;
          // v1: this.caption = "Highlighted on the scan: " + label + (cell.dataset.line !== undefined ? " (line " + (Number(cell.dataset.line) + 1) + ")" : "") + ".";
          this.caption = "Showing on the scan: " + label + (cell.dataset.line !== undefined ? " (item " + (Number(cell.dataset.line) + 1) + ")" : "") + ".";
          this.$nextTick(this.scrollBoxIntoView.bind(this));
        },

        // Scrolls only the scan viewport, never the window, so the field being edited stays put.
        scrollBoxIntoView: function () {
          if (!this.hasBox) return;
          var viewport = this.$refs.viewport;
          var s = this.scale;
          var top = this.box.top * s, bottom = top + this.box.height * s;
          var left = this.box.left * s, right = left + this.box.width * s;
          if (top < viewport.scrollTop || bottom > viewport.scrollTop + viewport.clientHeight) {
            viewport.scrollTop = Math.max(0, top - viewport.clientHeight / 3);
          }
          if (left < viewport.scrollLeft || right > viewport.scrollLeft + viewport.clientWidth) {
            viewport.scrollLeft = Math.max(0, left - viewport.clientWidth / 4);
          }
        },

        onKeydown: function (event) {
          var input = event.target;
          if (!input.classList || !input.classList.contains("cell-input")) return;
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          var target = null;
          switch (event.key) {
            case "ArrowDown":
              target = this.verticalNeighbor(input, 1);
              break;
            case "ArrowUp":
              target = this.verticalNeighbor(input, -1);
              break;
            case "Enter":
              event.preventDefault();
              target = this.verticalNeighbor(input, event.shiftKey ? -1 : 1);
              // if (!target) input.dispatchEvent(new Event("change", { bubbles: true }));
              // Last field: no focus move to trigger the save, so fire it directly, but only for a real edit (any PATCH confirms the field).
              if (!target && input.value !== input.defaultValue) input.dispatchEvent(new Event("change", { bubbles: true }));
              break;
            case "ArrowLeft":
              if (input.selectionStart === 0 && input.selectionEnd === 0) target = this.domNeighbor(input, -1);
              break;
            case "ArrowRight":
              if (input.selectionStart === input.value.length && input.selectionEnd === input.value.length) target = this.domNeighbor(input, 1);
              break;
            case "Escape":
              if (input.value !== input.defaultValue) {
                input.value = input.defaultValue;
                event.preventDefault();
              }
              return;
            default:
              return;
          }
          if (target) {
            event.preventDefault();
            focusInput(target);
          }
        },

        domNeighbor: function (input, step) {
          var inputs = cellInputs(this.$root);
          var index = inputs.indexOf(input);
          return index < 0 ? null : inputs[index + step] || null;
        },

        // Same column in the previous/next line; header and totals fields fall back to document order.
        verticalNeighbor: function (input, step) {
          var cell = input.closest(".field-cell");
          if (cell && cell.dataset.line !== undefined) {
            var line = Number(cell.dataset.line) + step;
            var match = this.$root.querySelector('.field-cell[data-line="' + line + '"][data-field="' + cell.dataset.field + '"] .cell-input');
            if (match) return match;
          }
          return this.domNeighbor(input, step);
        },

        jumpToNextUnchecked: function () {
          var pending = Array.prototype.slice.call(this.$root.querySelectorAll(".field-cell[data-attention] .cell-input"));
          var announcer = document.getElementById("review-announcer");
          if (!pending.length) {
            if (announcer) announcer.textContent = "No fields left to check.";
            return;
          }
          var reference = lastFocusedInput && document.body.contains(lastFocusedInput) ? lastFocusedInput : null;
          var next = null;
          if (reference) {
            next = pending.find(function (el) {
              return reference.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING;
            });
          }
          next = next || pending[0];
          next.scrollIntoView({ block: "center" });
          focusInput(next);
        },
      };
    });

    // Rows are plain DOM (cloned from a <template>), so server-rendered rows after a validation error need no seeding.
    window.Alpine.data("templateBuilder", function () {
      return {
        onFieldChange: onFieldChange,
        init: function () {
          resyncCustomText(this.$refs.rows);
        },
        builderRows: function () {
          return Array.prototype.slice.call(this.$refs.rows.querySelectorAll(".builder-row"));
        },
        addRow: function () {
          var row = this.$refs.rowTemplate.content.firstElementChild.cloneNode(true);
          this.$refs.rows.appendChild(row);
          row.querySelector("input").focus();
        },
        removeRow: function (event) {
          var row = event.target.closest(".builder-row");
          var rows = this.builderRows();
          if (rows.length === 1) {
            // Keep one row to type into; clearing it is the same as removing it.
            row.querySelector("input").value = "";
            row.querySelector("select").value = "";
            row.querySelector(".custom-text").value = "";
            syncCustomText(row.querySelector("select"), false);
            row.querySelector("input").focus();
            return;
          }
          var neighbor = rows[rows.indexOf(row) + 1] || rows[rows.indexOf(row) - 1];
          row.remove();
          neighbor.querySelector("input").focus();
        },
      };
    });

    window.Alpine.data("columnMapping", function () {
      return {
        onFieldChange: onFieldChange,
        init: function () {
          resyncCustomText(this.$el);
        },
      };
    });
  });

  // ---------- htmx: request errors ----------

  var failedElement = null;

  function showRequestError(message, element) {
    var box = document.getElementById("request-error");
    if (!box) return;
    box.querySelector(".request-error__text").textContent = message;
    box.hidden = false;
    failedElement = element || null;
  }

  function hideRequestError() {
    var box = document.getElementById("request-error");
    if (box) box.hidden = true;
    failedElement = null;
  }

  function describeFailure(element, base) {
    var cell = element && element.closest ? element.closest(".field-cell") : null;
    if (cell) {
      cell.classList.add("field-cell--save-failed");
      return "Your change to " + cellLabel(cell) + " was not saved. " + base;
    }
    return base;
  }

  document.addEventListener("htmx:sendError", function (event) {
    showRequestError(describeFailure(event.detail.elt, "Couldn't reach the server. Check that the app is still running, then try again."), event.detail.elt);
  });
  document.addEventListener("htmx:timeout", function (event) {
    showRequestError(describeFailure(event.detail.elt, "The server took too long to answer. Try again."), event.detail.elt);
  });
  document.addEventListener("htmx:responseError", function (event) {
    var status = event.detail.xhr ? event.detail.xhr.status : "unknown";
    // v1: "The server couldn't complete that request (error " + status + "). Try again; if it keeps failing, reload the page."
    showRequestError(describeFailure(event.detail.elt, "Something went wrong (error " + status + "). Try again; if it keeps happening, reload the page."), event.detail.elt);
  });
  // Only a later success from the same element (a recovered poll, a re-saved field) clears the alert.
  document.addEventListener("htmx:afterRequest", function (event) {
    if (event.detail.successful && failedElement && failedElement === event.detail.elt) hideRequestError();
  });
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest(".request-error__dismiss")) hideRequestError();
  });

  // ---------- htmx: keep keyboard focus sensible across swaps ----------

  var pendingFocus = null;

  document.addEventListener("htmx:beforeRequest", function (event) {
    var elt = event.detail.elt;
    var cell = elt.closest ? elt.closest(".field-cell") : null;
    pendingFocus = {
      cellId: cell ? cell.id : null,
      after: elt.getAttribute ? elt.getAttribute("data-focus-after") : null,
    };
  });

  // Only restore focus the swap itself destroyed; if the user had clicked elsewhere, leave it alone.
  document.addEventListener("htmx:beforeSwap", function (event) {
    if (!pendingFocus) return;
    var target = event.detail.target;
    pendingFocus.focusWasInTarget = !!(target && target.contains(document.activeElement));
  });

  document.addEventListener("htmx:afterSettle", function () {
    var request = pendingFocus;
    pendingFocus = null;
    if (!request || !request.focusWasInTarget) return;
    // htmx re-focuses a same-id replacement (e.g. #add-line); only an explicit data-focus-after overrides that.
    var focusLost = !document.activeElement || document.activeElement === document.body;
    if (!focusLost && !request.after) return;
    var target = null;
    if (request.after === "last-line") {
      var rows = document.querySelectorAll(".line-row");
      if (rows.length) target = rows[rows.length - 1].querySelector(".cell-input");
    } else if (request.after === "add-line") {
      target = document.getElementById("add-line");
    } else if (request.after) {
      // Any other value is an element id, e.g. the batch-list row whose action button was just replaced.
      target = document.getElementById(request.after);
    } else if (request.cellId) {
      // The focused control (usually "This is correct") was swapped away; continue to the next field.
      var cell = document.getElementById(request.cellId);
      var editor = document.getElementById("review-editor");
      if (cell && editor) target = inputAfter(editor, cell) || cell.querySelector(".cell-input");
    }
    if (target) {
      if (target.classList.contains("cell-input")) focusInput(target);
      else target.focus();
    }
  });

  // Re-announce the counter after an out-of-band swap; a replaced live region is often not read out.
  document.addEventListener("htmx:oobAfterSwap", function (event) {
    var target = event.detail.target;
    if (!target || target.id !== "review-summary") return;
    var announcer = document.getElementById("review-announcer");
    var count = target.querySelector(".review-summary__count");
    if (announcer && count) announcer.textContent = count.textContent.replace(/\s+/g, " ").trim();
  });

  // ---------- File dropzones: show the chosen file and a drag-over highlight ----------

  function syncDropzone(input) {
    var zone = input.closest(".dropzone");
    var chosen = zone.querySelector(".dropzone__chosen");
    var file = input.files && input.files[0];
    zone.classList.toggle("dropzone--chosen", !!file);
    chosen.hidden = !file;
    chosen.textContent = file ? "Selected: " + file.name : "";
  }

  function dropzoneInput(event) {
    var target = event.target;
    return target.classList && target.classList.contains("dropzone__input") ? target : null;
  }

  document.addEventListener("change", function (event) {
    var input = dropzoneInput(event);
    if (input) syncDropzone(input);
  });
  ["dragenter", "dragover"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var input = dropzoneInput(event);
      if (input) input.closest(".dropzone").classList.add("dropzone--active");
    });
  });
  ["dragleave", "drop"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var input = dropzoneInput(event);
      if (input) input.closest(".dropzone").classList.remove("dropzone--active");
    });
  });
  // Back/forward-cache can restore a chosen file without firing change.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".dropzone__input").forEach(syncDropzone);
  });

  // ---------- Plain (non-htmx) forms: prevent double submits ----------

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute || !form.hasAttribute("data-disable-on-submit") || event.defaultPrevented) return;
    // Deferred so the browser has already captured the form data before buttons are disabled.
    setTimeout(function () {
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("button[type=submit]").forEach(function (button) { button.disabled = true; });
    }, 0);
  });
  // Back-button restores pages from bfcache with buttons still disabled.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-disable-on-submit]").forEach(function (form) {
      form.removeAttribute("aria-busy");
      form.querySelectorAll("button[type=submit]").forEach(function (button) { button.disabled = false; });
    });
  });
})();
