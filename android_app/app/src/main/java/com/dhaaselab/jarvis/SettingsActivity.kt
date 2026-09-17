package com.dhaaselab.jarvis

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

/**
 * Tier 7's settings screen — shows which address the app actually last connected through,
 * and lets Devin edit the local/VPN addresses and computer name without a rebuild (the
 * spec's own explicit requirement, mirroring desktop_app's own "edit two constants, no
 * rebuild needed for anything else" design for its server-autostart config).
 */
class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        // Same targetSdk-35-mandatory-edge-to-edge fix as MainActivity — see that file's
        // own comment for the full story. This screen's Save button sits at the bottom of
        // a ScrollView, exactly where a phone's gesture nav bar would otherwise cover it.
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.settingsRootLayout)) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        val connectedViaText: TextView = findViewById(R.id.connectedViaText)
        val computerNameInput: EditText = findViewById(R.id.computerNameInput)
        val localAddressInput: EditText = findViewById(R.id.localAddressInput)
        val localHttpsPortInput: EditText = findViewById(R.id.localHttpsPortInput)
        val vpnAddressInput: EditText = findViewById(R.id.vpnAddressInput)
        val saveButton: Button = findViewById(R.id.saveButton)

        computerNameInput.setText(ConnectionPrefs.computerName(this))
        localAddressInput.setText(ConnectionPrefs.localAddress(this))
        localHttpsPortInput.setText(ConnectionPrefs.localHttpsPort(this))
        vpnAddressInput.setText(ConnectionPrefs.vpnAddress(this))

        // Re-checks reachability live rather than just showing whatever MainActivity last
        // resolved — this screen is exactly where you'd come to answer "why isn't this
        // working," so it should reflect the current, real state, not a stale one.
        connectedViaText.text = "checking…"
        lifecycleScope.launch {
            // attemptWake=false: a status screen should answer instantly, not trigger a
            // 30-second wake-and-retry cycle just from being opened.
            when (val result = ConnectionManager.resolve(this@SettingsActivity, attemptWake = false)) {
                is ConnectionManager.Resolution.Success -> {
                    val viaLabel = if (result.via == ConnectionManager.Via.LOCAL) "Local network" else "VPN (Tailscale)"
                    connectedViaText.text = "$viaLabel — ${result.baseUrl}"
                }
                is ConnectionManager.Resolution.Unreachable -> {
                    connectedViaText.text = "Not currently reachable"
                }
            }
        }

        saveButton.setOnClickListener {
            ConnectionPrefs.save(
                this,
                computerNameInput.text.toString().trim(),
                localAddressInput.text.toString().trim(),
                vpnAddressInput.text.toString().trim(),
                localHttpsPortInput.text.toString().trim(),
            )
            finish()  // MainActivity's onResume() re-resolves against the new addresses
        }
    }
}
