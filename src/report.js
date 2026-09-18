/** Post results back to the server, for a browser that cannot be driven from outside -
 *  a phone's WebView, where there is no marionette and no chromedriver. Only when the
 *  page was asked to, with ?report=1. */
export async function report(results) {
    if (new URLSearchParams(location.search).get("report") === null)
        return;
    try {
        await fetch("/report", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(results),
        });
    } catch (e) {
        // leave a trace in the page, since there is no console to read on a device
        document.title = "report failed: " + e;
    }
}
