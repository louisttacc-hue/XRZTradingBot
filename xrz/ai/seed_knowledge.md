# Grundwissen XRZ

## Oberste Regel: Kapitalerhalt
Kein System gewinnt immer. Ziel ist ein positiver Erwartungswert über viele Trades bei kontrolliertem Risiko. Wer Verluste klein hält, bleibt im Spiel. Maximal 0,5–2 % des Kontos pro Trade riskieren. Nach einem Drawdown von 50 % braucht man +100 %, um wieder bei null zu sein – deshalb Notbremsen (Tagesverlust-Limit, maximaler Drawdown) strikt einhalten. Niemals Verluste "aussitzen" oder Stops nach hinten verschieben.

## Positionsgröße nach Risiko
Positionsgröße = (Kontowert × Risiko-%) ÷ Abstand zwischen Einstieg und Stop-Loss. Beispiel: 10.000 € Konto, 1 % Risiko = 100 €. Stop 2 % unter Einstieg → Positionsgröße 5.000 €. Bei hoher Volatilität (großer ATR) wird die Position automatisch kleiner. Nie mehr als ein festgelegter Anteil (z. B. 25 %) des Kontos in eine einzelne Position.

## Stop-Loss und Take-Profit
Stops gehören dorthin, wo die Trade-Idee widerlegt ist – nicht dorthin, wo der Verlust "angenehm" ist. Der ATR-Stop (z. B. 2 × ATR) passt sich der Volatilität an. Take-Profit als Vielfaches des Risikos (R): Bei 2,5R reicht eine Trefferquote von ca. 30 %, um profitabel zu sein. Ab +1R den Stop auf Einstand ziehen, danach Trailing-Stop, um Gewinne laufen zu lassen.

## Marktphasen erkennen
Märkte wechseln zwischen Trend (gerichtet, ADX > 20–25) und Seitwärtsphase (ADX < 20, Kurs pendelt um gleitende Durchschnitte). Trendfolge-Strategien verdienen in Trends und verlieren in Seitwärtsphasen; Mean-Reversion-Strategien umgekehrt. Phasen niedriger Volatilität (Bollinger-Squeeze) gehen oft starken Bewegungen voraus. Die wichtigste Frage vor jedem Trade: Welche Phase haben wir gerade?

## Multi-Timeframe-Analyse
Den übergeordneten Zeitrahmen (z. B. 4h oder Tag) für die Richtung nutzen, den kleineren (z. B. 1h) für das Timing. Long-Trades gegen einen klaren übergeordneten Abwärtstrend haben eine deutlich schlechtere Erfolgsquote. "Der Trend ist dein Freund" gilt vor allem auf höheren Zeitrahmen.

## RSI richtig lesen
RSI über 70 heißt nicht automatisch "verkaufen": In starken Aufwärtstrends bleibt der RSI lange überkauft. In Trends sind Rücksetzer des RSI auf 40–50 oft Kaufgelegenheiten (Bull-Range 40–80), in Abwärtstrends Erholungen auf 50–60 Verkaufsgelegenheiten. Divergenzen (Kurs macht neues Hoch, RSI nicht) warnen vor nachlassendem Momentum, sind aber allein kein Einstiegssignal.

## MACD
MACD = Differenz aus EMA12 und EMA26, Signallinie = EMA9 des MACD. Kreuzungen in Trendrichtung sind wertvoller als gegen den Trend. Das Histogramm zeigt die Beschleunigung: schrumpfendes Histogramm = nachlassendes Momentum. MACD hinkt hinterher und erzeugt in Seitwärtsphasen viele Fehlsignale.

## Bollinger-Bänder
Mittellinie = SMA20, Bänder = ±2 Standardabweichungen. In Seitwärtsphasen dienen die Bänder als Umkehrzonen, in Trends "läuft" der Kurs am Band entlang. Ein Squeeze (sehr schmale Bänder) kündigt einen Ausbruch an; die Richtung des ersten starken Schlusskurses außerhalb der Bänder ist entscheidend, idealerweise mit erhöhtem Volumen.

## ATR und ADX
ATR (Average True Range) misst die durchschnittliche Schwankung und ist die Basis für Stops und Positionsgröße. ADX misst die Trendstärke unabhängig von der Richtung: unter 20 schwach/seitwärts, 25–40 klarer Trend, über 50 sehr stark (oft späte Phase). +DI über −DI zeigt Aufwärtsdruck.

## Volumen
Ausbrüche mit hohem Volumen sind glaubwürdiger. Steigende Kurse bei fallendem Volumen deuten auf nachlassendes Interesse. On-Balance-Volume (OBV), das vor dem Kurs neue Hochs macht, ist ein positives Zeichen. Sehr hohe Volumenspitzen nach langen Bewegungen markieren oft Erschöpfung (Climax).

## Unterstützung und Widerstand
Zonen, an denen der Kurs mehrfach gedreht hat, sind wichtig. Je öfter getestet, desto bedeutsamer – aber auch desto wahrscheinlicher irgendwann gebrochen. Gebrochener Widerstand wird oft zur Unterstützung (Rollentausch). Runde Zahlen (z. B. 100.000 bei BTC) wirken psychologisch. Stops nicht genau auf, sondern etwas hinter solche Zonen legen.

## Krypto-Besonderheiten
Krypto handelt 24/7; am Wochenende ist die Liquidität geringer und Bewegungen können abrupter sein. Altcoins korrelieren stark mit Bitcoin – mehrere Long-Positionen in Altcoins sind praktisch EIN großer Bitcoin-Trade. Der Fear-&-Greed-Index zeigt die Stimmung: extreme Gier ist oft spät im Zyklus, extreme Angst oft eine Chance für langfristige Käufe. Hebel vervielfacht nicht nur Gewinne, sondern vor allem das Risiko der Liquidation.

## Makro-Termine
Zinsentscheidungen (FED/FOMC, EZB), US-Inflationsdaten (CPI), Arbeitsmarktdaten (NFP) und große Regulierungsnachrichten sorgen für Volatilitätsspitzen. Kurz vor solchen Terminen keine neuen großen Positionen eröffnen oder Positionen verkleinern. Technische Signale sind in diesen Momenten weniger zuverlässig.

## Backtesting-Fallen
Overfitting: Zu viele optimierte Parameter passen perfekt zur Vergangenheit und versagen in der Zukunft – deshalb Walk-Forward (Parameter auf alten Daten wählen, auf neuen prüfen). Lookahead-Bias: Signale dürfen nur Daten bis zur aktuellen Kerze nutzen und werden erst zur nächsten Kerze ausgeführt. Gebühren und Slippage immer einrechnen. Wenige Trades im Backtest = keine statistische Aussagekraft.

## Trading-Psychologie
Die häufigsten Fehler: Rache-Trading nach Verlusten, zu große Positionen aus Gier, Gewinne zu früh mitnehmen und Verluste laufen lassen, Regeln während des Trades ändern. Ein Bot hilft, weil er Regeln emotionslos befolgt – aber nur, wenn man ihn nicht ständig manuell übersteuert. Nach einer Verlustserie: Größe reduzieren, nicht erhöhen.

## Checkliste vor Live-Trading
1. Mindestens mehrere Wochen Paper-Trading mit positiven Ergebnissen und genug Trades (> 30). 2. Börsen-API-Key OHNE Auszahlungsrechte, idealerweise mit IP-Beschränkung. 3. Erst mit kleinem Betrag und niedrigem Live-Order-Limit starten. 4. Tagesverlust-Limit und Notbremse aktiv. 5. Nur Geld einsetzen, dessen Verlust man verkraften kann. 6. Den Mac wach halten (der Bot muss laufen, um Stops zu überwachen) – zusätzlich Stops an der Börse platzieren.

## Steuern in Deutschland (Krypto, ohne Gewähr)
Gewinne aus privaten Krypto-Verkäufen sind nach über einem Jahr Haltedauer steuerfrei; darunter steuerpflichtig, sofern die Gewinne aus privaten Veräußerungsgeschäften im Jahr die Freigrenze (seit 2024: 1.000 €) erreichen. Jeder Trade muss dokumentiert werden – der Bot speichert alle Trades in seiner Datenbank. Dies ist keine Steuerberatung.

## Wie XRZ Entscheidungen trifft
1. Daten laden (Zeitrahmen + übergeordneter Zeitrahmen). 2. Indikatoren, Marktphase, Unterstützungen/Widerstände berechnen. 3. Sieben Strategien bewerten; jede bekommt ein Gewicht aus dem Walk-Forward-Backtest und aus den Ergebnissen echter Trades. 4. Gewichteter Ensemble-Score von −1 bis +1, gefiltert durch den übergeordneten Trend und TradingView-Alerts. 5. Die KI prüft den Kandidaten (darf ablehnen oder verkleinern, nie vergrößern). 6. Das Risiko-Management berechnet die Positionsgröße und hat das letzte Wort. 7. Nach jedem Trade wird eine Lektion gespeichert.
