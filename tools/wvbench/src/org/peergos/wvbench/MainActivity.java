package org.peergos.wvbench;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebSettings;
import android.util.Log;

/** Nothing but a WebView pointed at a url, so the benchmark can be run in the real
 *  Android WebView rather than in Chrome, which is a different app with the same engine. */
public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        WebView.setWebContentsDebuggingEnabled(true);
        WebView web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                Log.i("wvbench", m.message());
                return true;
            }
        });
        setContentView(web);
        String url = getIntent().getData() != null ? getIntent().getData().toString()
                : "http://10.0.2.2:8000/bench/?report=1";
        Log.i("wvbench", "loading " + url);
        web.loadUrl(url);
    }
}
