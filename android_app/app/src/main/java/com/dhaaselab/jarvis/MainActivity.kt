package com.dhaaselab.jarvis

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Message
import android.view.View
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Tier 7 — the bare WebView shell. Resolves which address actually reaches the main
 * computer (local network first, then Tailscale VPN — see ConnectionManager), shows a
 * status overlay while that's in progress (or on failure), and otherwise gets out of the
 * way: the WebView is just Jarvis's own web UI, unmodified, same as the desktop app's
 * pywebview window is just a native frame around the same server.
 */
class MainActivity : AppCompatActivity() {

    companion object {
        /** Set by JarvisFirebaseMessagingService on a notification tap (Tier 10) — read in
         * connect() to append ?conv=<id> to whatever URL actually gets loaded, the same
         * deep-link shape app.js's own _handleDeepLinkOnLoad() already expects. */
        const val EXTRA_DEEP_LINK_CONV = "deep_link_conv"
    }

    // Set in connect()'s success branch — checkNativeShellVersion() (called from
    // onPageFinished, which doesn't otherwise know which of local/VPN actually resolved)
    // needs this to build the right /native-shell-version URL.
    private var currentBaseUrl: String? = null

    private lateinit var webView: WebView
    private lateinit var statusOverlay: View
    private lateinit var statusText: TextView
    private lateinit var progressSpinner: ProgressBar
    private lateinit var retryButton: Button
    private lateinit var settingsButtonFromError: Button
    private lateinit var settingsIconButton: View

    // Only re-resolve/reload when actually returning from Settings having saved a change
    // (SettingsActivity finish()es normally either way, so a plain onResume() check can't
    // tell "backgrounded and resumed" apart from "just edited the address" without this) —
    // avoids reloading the WebView (losing scroll position/composer draft) on every
    // ordinary app-switch-back, which the old onResume()-based check would have done.
    private val settingsLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        connect()
    }

    // Requesting this alone doesn't do anything for the WebView by itself — see
    // configureWebView()'s onPermissionRequest override, which is the piece that actually
    // matters for getUserMedia(). This just gets the OS-level grant in place first, so that
    // check has something to say yes to. No extra action needed on the callback itself: any
    // in-progress or future getUserMedia() call re-checks live via ContextCompat each time.
    private val requestMicPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val requestNotificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Real bug hit on a physical device (Samsung S24 Ultra) after bumping targetSdk to
        // 35 for Play Console's submission requirement: Android 15+ makes edge-to-edge
        // display mandatory once an app targets API 35 — content that used to sit safely
        // below the status bar and above the gesture nav bar now draws underneath both by
        // default. Nothing here opts back into the old letterboxed behavior (targetSdk 35
        // doesn't allow that); instead this pads the root layout by the actual system bar
        // insets, so the WebView's own content (its header, its composer's fixed-bottom
        // bar) renders inside the safe area instead of behind the phone's own status
        // bar/notification icons or its gesture bar. Applied to the root FrameLayout, not
        // the WebView directly, so the floating settings button and the status/error
        // overlay both get the same safe-area treatment for free.
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.rootLayout)) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        webView = findViewById(R.id.webView)
        statusOverlay = findViewById(R.id.statusOverlay)
        statusText = findViewById(R.id.statusText)
        progressSpinner = findViewById(R.id.progressSpinner)
        retryButton = findViewById(R.id.retryButton)
        settingsButtonFromError = findViewById(R.id.settingsButtonFromError)
        settingsIconButton = findViewById(R.id.settingsIconButton)

        // Debug-only (checks the app's own debuggable flag, not a hardcoded constant — never
        // true in a release/signed build regardless of forgetting to strip this) — lets
        // chrome://inspect (or a raw devtools-protocol connection) attach to this WebView,
        // used to verify Tier 9's onCreateWindow actually fires on a real window.open() call.
        if ((applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        configureWebView()

        retryButton.setOnClickListener { connect() }
        val openSettings = { settingsLauncher.launch(Intent(this, SettingsActivity::class.java)) }
        settingsButtonFromError.setOnClickListener { openSettings() }
        settingsIconButton.setOnClickListener { openSettings() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack() && statusOverlay.visibility != View.VISIBLE) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        ensureMicPermission()
        ensureNotificationPermission()
        connect()
    }

    /** singleTop (see the manifest) means a notification tap while already running lands
     * here instead of a fresh onCreate() — update the held intent (so connect() below sees
     * the new deep-link extra) and re-resolve/reload with it. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        connect()
    }

    /** Tier 10: Android 13+ (API 33) requires this to show ANY notification at all — a
     * real, separate permission from RECORD_AUDIO that's easy to forget since nothing about
     * push notifications otherwise prompts for it. Below API 33 it's granted at install
     * time automatically, so this is a no-op there. Same "ask once, respect no" shape as
     * ensureMicPermission(), no rationale dialog needed — the settings modal equivalent
     * (Android's own per-app notification settings) already exists system-wide. */
    private fun ensureNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.TIRAMISU) return
        val alreadyGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!alreadyGranted) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    /** Tier 8: requested proactively on launch — before the WebView ever calls
     * getUserMedia() — with a plain-language explanation first if Android thinks one is
     * warranted (shouldShowRequestPermissionRationale), rather than only reacting the first
     * time voice input is actually tapped. "Not now" is a real, respected no: nothing here
     * force-prompts again this session — the standard Android permission UX already covers
     * "try again later" via the system Settings page if changed. */
    private fun ensureMicPermission() {
        val alreadyGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (alreadyGranted) return

        if (shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)) {
            AlertDialog.Builder(this)
                .setTitle(getString(R.string.mic_rationale_title))
                .setMessage(getString(R.string.mic_rationale_body))
                .setPositiveButton(getString(R.string.mic_rationale_continue)) { _, _ ->
                    requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
                }
                .setNegativeButton(getString(R.string.mic_rationale_not_now), null)
                .show()
        } else {
            requestMicPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun onResume() {
        super.onResume()
        // Belt-and-suspenders for the case connect() never got to load anything at all
        // (e.g. process was restarted while still on the status overlay) — the Settings-
        // return case is handled separately by settingsLauncher above, not here, so an
        // ordinary app-switch-back doesn't reload an already-working WebView.
        if (::webView.isInitialized && webView.url == null) {
            connect()
        }
    }

    private fun configureWebView() {
        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true   // app.js uses localStorage for the active conversation id
        settings.databaseEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false  // matches a normal desktop browser tab's default for this app's own voice features
        // Tier 9 prerequisite: without these two, window.open() (OAuth pop-ups, "open
        // externally" links) is simply a no-op in Android WebView — onCreateWindow below
        // never even fires. This is the "silently does nothing" bug the spec calls out;
        // these settings are the other half of the fix, not optional polish.
        settings.setSupportMultipleWindows(true)
        settings.javaScriptCanOpenWindowsAutomatically = true

        // A bare WebView with no client at all hands EVERY navigation to the system
        // browser, including ordinary same-site link clicks inside the SPA — the opposite
        // of what a normal browser tab does. Overriding shouldOverrideUrlLoading to return
        // false keeps in-app navigation inside this WebView; onCreateWindow below is the
        // actual pop-up/OAuth escape hatch, a separate code path from ordinary navigation.
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = false

            // Tier 10: the real, reliable point to inject the FCM token — app.js's own
            // window.registerFcmToken doesn't exist in the page's JS context until the
            // page has actually finished loading (loadUrl() itself is fire-and-forget).
            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                injectFcmTokenIfAvailable()
                checkNativeShellVersion()
            }
        }

        // Tier 8, second half: even with RECORD_AUDIO held at the OS level (ensureMicPermission
        // above), a WebView denies getUserMedia() outright unless this override explicitly
        // grants it — there's no default "ask the user" prompt the way a real browser tab
        // has; an unhandled onPermissionRequest is a silent deny. Grants ONLY the audio
        // resource, deliberately checked resource-by-resource rather than
        // request.grant(request.resources) blanket-granting everything asked for — this app
        // never requests CAMERA, so a page that also asked for RESOURCE_VIDEO_CAPTURE
        // shouldn't get it just because it happened to be bundled in the same request.
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                val hasRuntimeGrant = ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED

                val toGrant = request.resources.filter {
                    it == PermissionRequest.RESOURCE_AUDIO_CAPTURE && hasRuntimeGrant
                }
                if (toGrant.isNotEmpty()) {
                    request.grant(toGrant.toTypedArray())
                } else {
                    request.deny()
                }
            }

            // Tier 9: window.open() (Google's OAuth consent screen, or an explicit "open
            // externally" link) has no target of its own to load into — a bare WebView
            // just silently drops it. The documented way to actually get the destination
            // URL back out is genuinely this roundabout: create a second, never-shown
            // WebView, attach a client that intercepts the first real navigation attempt
            // on it, and use the transport message to receive the URL that would have
            // loaded — there's no synchronous "here's the URL" callback on this API.
            // Once we have it, hand it to a Custom Tab instead of ever showing that second
            // WebView. OAuth still completes because the provider's callback lands back
            // on Jarvis's own server (server.py's existing redirect handling), not on
            // anything this app needs to intercept.
            override fun onCreateWindow(
                view: WebView,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: Message,
            ): Boolean {
                val popupWebView = WebView(this@MainActivity)
                popupWebView.webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                        launchCustomTab(request.url)
                        return true
                    }

                    override fun onPageStarted(v: WebView, url: String, favicon: android.graphics.Bitmap?) {
                        launchCustomTab(Uri.parse(url))
                    }
                }

                val transport = resultMsg.obj as WebView.WebViewTransport
                transport.webView = popupWebView
                resultMsg.sendToTarget()
                return true
            }
        }
    }

    private fun launchCustomTab(uri: Uri) {
        CustomTabsIntent.Builder().build().launchUrl(this, uri)
    }

    /** Tier 10: hands whatever FCM token this device currently has to app.js's
     * window.registerFcmToken(), which sends it to the server over the already-
     * authenticated WS connection (see server.py's register_fcm_token handler). Two
     * sources, in order: (1) a token already saved by JarvisFirebaseMessagingService's
     * onNewToken() — the common case; (2) if none is saved yet (e.g. first launch, before
     * any onNewToken callback has fired), ask FirebaseMessaging directly for the current
     * one. Both are no-ops, not crashes, if google-services.json was never set up — see
     * the module-level try/catch below and android_app/README.md. */
    private fun injectFcmTokenIfAvailable() {
        val savedToken = FcmTokenStore.getToken(this)
        if (savedToken != null && !FcmTokenStore.isRegistered(this)) {
            sendTokenToWebView(savedToken)
            return
        }
        if (savedToken != null) return  // already registered, nothing new to do

        try {
            com.google.firebase.messaging.FirebaseMessaging.getInstance().token
                .addOnSuccessListener { token ->
                    FcmTokenStore.saveToken(this, token)
                    sendTokenToWebView(token)
                }
        } catch (e: IllegalStateException) {
            // FirebaseApp not initialized — no google-services.json yet. Expected, not an
            // error: this app works fully without push notifications until Tier 10's setup
            // is actually done (see android_app/README.md).
        }
    }

    private fun sendTokenToWebView(token: String) {
        val escaped = token.replace("\\", "\\\\").replace("'", "\\'")
        webView.evaluateJavascript("window.registerFcmToken && window.registerFcmToken('$escaped')") {
            FcmTokenStore.markRegistered(this)
        }
    }

    /** Part 3 — "you're out of date" check, the Android half of server.py's
     * DESKTOP_MIN_VERSION/android_min_version_code mechanism. Only ever fires for a
     * native-shell change (this app's own Kotlin) — a web/dashboard-only server change
     * needs nothing here, the WebView already shows the latest UI on every load regardless
     * of this app's own version. Injects a small banner into the page (same idea as
     * desktop_app's own JS-injected banner, kept consistent across both native shells)
     * rather than a native Android view, since there's no persistent chrome of this app's
     * own to add one to outside the WebView content. */
    private fun checkNativeShellVersion() {
        val baseUrl = currentBaseUrl ?: return
        lifecycleScope.launch {
            val minVersionCode = withContext(Dispatchers.IO) {
                try {
                    val connection = java.net.URL("${baseUrl}native-shell-version").openConnection() as java.net.HttpURLConnection
                    connection.connectTimeout = 3000
                    connection.readTimeout = 3000
                    connection.inputStream.bufferedReader().use { reader ->
                        val json = org.json.JSONObject(reader.readText())
                        json.optInt("android_min_version_code", -1)
                    }
                } catch (e: Exception) {
                    -1  // server unreachable or malformed response — not worth a banner over, just skip until the next page load
                }
            }
            if (minVersionCode <= BuildConfig.VERSION_CODE) return@launch

            val bannerJs = """
            (function() {
              if (document.getElementById('jarvis-android-update-banner')) return;
              var b = document.createElement('div');
              b.id = 'jarvis-android-update-banner';
              b.textContent = 'A new version of this app is ready in Play Store internal testing.';
              b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:#0a84ff;'
                + "color:#fff;font:13px -apple-system,Roboto,sans-serif;text-align:center;padding:6px;";
              document.body.appendChild(b);
            })();
            """.trimIndent()
            webView.evaluateJavascript(bannerJs, null)
        }
    }

    private fun connect() {
        showStatus(getString(R.string.status_connecting), showRetry = false)
        lifecycleScope.launch {
            val local = ConnectionPrefs.localAddress(this@MainActivity)
            showStatus(getString(R.string.status_trying_local, local), showRetry = false)

            // onStatus reports live progress during Tier 7a's wake-and-retry window
            // ("Waking Jarvis… (Ns)") — same role as desktop_app's splash countdown.
            val result = ConnectionManager.resolve(this@MainActivity) { statusUpdate ->
                showStatus(statusUpdate, showRetry = false)
            }

            when (result) {
                is ConnectionManager.Resolution.Success -> {
                    // Tier 10 deep link: a notification tap set this extra (see
                    // JarvisFirebaseMessagingService + onNewIntent above) — consumed once
                    // (removeExtra) so backgrounding and returning later doesn't keep
                    // re-jumping to a stale conversation on every reconnect.
                    val deepLinkConv = intent.getStringExtra(EXTRA_DEEP_LINK_CONV)
                    intent.removeExtra(EXTRA_DEEP_LINK_CONV)
                    val url = if (deepLinkConv != null) "${result.baseUrl}?conv=$deepLinkConv" else result.baseUrl

                    currentBaseUrl = result.baseUrl
                    webView.loadUrl(url)
                    statusOverlay.visibility = View.GONE
                    // FCM token injection happens from webViewClient's onPageFinished
                    // below, not here — loadUrl() is async and window.registerFcmToken
                    // doesn't exist in the page's JS context until app.js has actually run.
                }
                is ConnectionManager.Resolution.Unreachable -> {
                    val name = ConnectionPrefs.computerName(this@MainActivity)
                    val message = if (result.wolAttempted) {
                        getString(R.string.status_unreachable_wol_tried, name)
                    } else {
                        getString(R.string.status_unreachable_no_wol, name)
                    }
                    showStatus(message, showRetry = true)
                }
            }
        }
    }

    private fun showStatus(text: String, showRetry: Boolean) {
        statusOverlay.visibility = View.VISIBLE
        statusText.text = text
        progressSpinner.visibility = if (showRetry) View.GONE else View.VISIBLE
        retryButton.visibility = if (showRetry) View.VISIBLE else View.GONE
        settingsButtonFromError.visibility = if (showRetry) View.VISIBLE else View.GONE
    }
}
