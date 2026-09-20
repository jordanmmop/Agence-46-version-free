package com.agence.financiere;

import android.annotation.SuppressLint;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.KeyEvent;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

/**
 * Agence Numérique Financière — application Android.
 *
 * WebView plein écran connectée au serveur qui tourne sur le PC
 * (AgenceNumerique.exe ou start.py). Au premier lancement, on demande
 * l'adresse affichée par l'app bureau, ex. http://192.168.1.20:8765
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "agence";
    private static final String KEY_URL = "server_url";

    private WebView web;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        String url = prefs.getString(KEY_URL, "");
        if (url.isEmpty()) {
            afficherEcranConfig("");
        } else {
            ouvrirApp(url);
        }
    }

    /** Écran de saisie de l'adresse du serveur (premier lancement / erreur). */
    private void afficherEcranConfig(String message) {
        // Détache la WebView : sinon le bouton retour resterait capturé
        // par son historique invisible (onKeyDown teste web.canGoBack()).
        web = null;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#0A0E1A"));
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad * 3, pad, pad);

        TextView titre = new TextView(this);
        titre.setText("📊 Agence Numérique Financière");
        titre.setTextColor(Color.parseColor("#64B5F6"));
        titre.setTextSize(20);
        root.addView(titre);

        TextView aide = new TextView(this);
        aide.setText("\nLancez AgenceNumerique.exe sur votre PC : "
                + "l'adresse à saisir s'affiche sur l'écran de démarrage "
                + "(carte « Sur votre iPhone / téléphone »).\n");
        aide.setTextColor(Color.parseColor("#9AADCC"));
        aide.setTextSize(14);
        root.addView(aide);

        if (!message.isEmpty()) {
            TextView err = new TextView(this);
            err.setText(message + "\n");
            err.setTextColor(Color.parseColor("#FF6B6B"));
            err.setTextSize(13);
            root.addView(err);
        }

        EditText champ = new EditText(this);
        champ.setHint("http://192.168.1.20:8765");
        champ.setText(prefs.getString(KEY_URL, ""));
        champ.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        champ.setTextColor(Color.WHITE);
        champ.setHintTextColor(Color.parseColor("#4A5A7A"));
        root.addView(champ);

        Button bouton = new Button(this);
        bouton.setText("Se connecter →");
        bouton.setBackgroundColor(Color.parseColor("#1565C0"));
        bouton.setTextColor(Color.WHITE);
        root.addView(bouton);

        bouton.setOnClickListener(v -> {
            String saisie = champ.getText().toString().trim();
            if (saisie.isEmpty()) return;
            if (!saisie.startsWith("http://") && !saisie.startsWith("https://")) {
                saisie = "http://" + saisie;
            }
            prefs.edit().putString(KEY_URL, saisie).apply();
            ouvrirApp(saisie);
        });

        setContentView(root);
    }

    /** WebView plein écran sur l'app. */
    @SuppressLint("SetJavaScriptEnabled")
    private void ouvrirApp(String url) {
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        web.setBackgroundColor(Color.parseColor("#0A0E1A"));

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // Erreur sur la page principale uniquement (pas une ressource)
                if (request.isForMainFrame()) {
                    afficherEcranConfig("Connexion impossible — vérifiez que "
                            + "l'application tourne sur le PC et que le téléphone "
                            + "est sur le même Wi-Fi.");
                }
            }
        });

        web.loadUrl(url);
        setContentView(web);
    }

    /** Bouton retour : navigation web, appui long conceptuel → config. */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
