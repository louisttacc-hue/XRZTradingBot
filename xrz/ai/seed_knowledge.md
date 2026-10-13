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
1. Mindestens mehrere Wochen Paper-Trading mit positiven Ergebnissen und genug Trades (> 30). 2. In TradingView prüfen, welches Konto aktiv ist: Paper Trading ist Spielgeld, ein verbundener Broker ist echtes Geld. 3. Erst mit kleinem Betrag und niedrigem Limit pro Order starten. 4. Tagesverlust-Limit und Notbremse aktiv. 5. Nur Geld einsetzen, dessen Verlust man verkraften kann. 6. Jede Order mit Stop-Loss beim Broker platzieren – nicht darauf verlassen, dass der Mac wach ist und Vincent den Stop überwacht.

## Steuern in Deutschland (Krypto, ohne Gewähr)
Gewinne aus privaten Krypto-Verkäufen sind nach über einem Jahr Haltedauer steuerfrei; darunter steuerpflichtig, sofern die Gewinne aus privaten Veräußerungsgeschäften im Jahr die Freigrenze (seit 2024: 1.000 €) erreichen. Jeder Trade muss dokumentiert werden – der Bot speichert alle Trades in seiner Datenbank. Dies ist keine Steuerberatung.

## Wie Vincent Entscheidungen trifft
1. Daten laden (Zeitrahmen + übergeordneter Zeitrahmen). 2. Indikatoren, Marktphase, Unterstützungen/Widerstände berechnen. 3. Über 35 Strategien (Trend, Momentum, Mean-Reversion, Ausbruch, Volumen, Kerzenmuster) bewerten; jede bekommt ein Gewicht aus dem Walk-Forward-Backtest und aus den Ergebnissen echter Trades. 4. Gewichteter Ensemble-Score von −1 bis +1, gefiltert durch den übergeordneten Trend und TradingView-Alerts. 5. Die KI prüft den Kandidaten (darf ablehnen oder verkleinern, nie vergrößern). 6. Das Risiko-Management berechnet die Positionsgröße und hat das letzte Wort. 7. Vincent schlägt den Trade vor; ausgeführt wird erst nach Freigabe – und nur in TradingView, nachdem das aktive Konto (Paper oder Live) geprüft wurde. 8. Nach jedem Trade wird eine Lektion gespeichert.

## Orderarten
Market-Order: sofort zum besten verfügbaren Preis – sicher ausgeführt, aber mit Slippage. Limit-Order: nur zum angegebenen Preis oder besser – kein Slippage, aber evtl. keine Ausführung. Stop-Order: wird bei Erreichen des Stop-Preises zur Market-Order (typisch für Stop-Loss und Ausbruchs-Einstiege); in schnellen Märkten kann der Ausführungspreis deutlich schlechter sein. Stop-Limit: wird zur Limit-Order – kein Slippage, aber Risiko, gar nicht ausgeführt zu werden. Bracket-Order: Einstieg mit fest verknüpftem Take-Profit und Stop-Loss (OCO: wird eines ausgeführt, verfällt das andere).

## Erwartungswert und R-Multiple
1R ist der Betrag, den man pro Trade riskiert (Abstand Einstieg–Stop × Stückzahl). Erwartungswert pro Trade in R = Trefferquote × Ø Gewinn in R − Verlustquote × Ø Verlust in R. Beispiel: 40 % Treffer mit Ø 2R Gewinn, 60 % Verluste mit Ø 1R → 0,4 × 2 − 0,6 × 1 = +0,2R pro Trade. Die Gewinnschwelle liegt bei 1 ÷ (1 + Chance-Risiko-Verhältnis): bei 2:1 also ca. 33 % Trefferquote – vor Gebühren und Slippage. Eine hohe Trefferquote allein sagt nichts, wenn die seltenen Verluste sehr groß sind.

## Verlustserien sind normal
Auch gute Systeme haben lange Verlustserien. Bei 40 % Trefferquote liegt die Wahrscheinlichkeit, in 200 Trades mindestens 8 Verluste in Folge zu erleben, bei rund 75 %. Deshalb pro Trade nur so viel riskieren, dass auch 10 Verluste hintereinander verkraftbar sind (bei 1 % Risiko etwa −10 %). Eine Verlustserie ist erst dann ein Warnsignal, wenn sie deutlich länger ist, als die Statistik des Systems erwarten lässt, oder wenn sich die Marktphase geändert hat.

## Marktstruktur
Aufwärtstrend = höhere Hochs und höhere Tiefs, Abwärtstrend = tiefere Hochs und tiefere Tiefs. Ein Strukturbruch (z. B. das letzte höhere Tief wird unterschritten) ist oft das erste Warnzeichen für eine Trendwende, bestätigt sich aber erst mit einem tieferen Hoch. Einstiege mit dem Trend nach einem Rücksetzer an ein früheres Ausbruchsniveau (Retest) haben meist ein besseres Chance-Risiko-Verhältnis als das Hinterherlaufen hinter einer großen Kerze.

## Gleitende Durchschnitte
SMA gewichtet alle Kerzen gleich, EMA gewichtet jüngere Kerzen stärker und reagiert schneller. Viel beachtet: EMA 20 (kurzfristiger Trend), SMA/EMA 50 (mittelfristig) und SMA 200 (langfristig; darüber gilt ein Markt als bullisch). Golden Cross = SMA 50 kreuzt SMA 200 nach oben, Death Cross = nach unten; beide sind späte Signale. In Seitwärtsphasen erzeugen Durchschnitts-Kreuzungen viele Fehlsignale.

## Stochastik und andere Oszillatoren
Die Stochastik (%K meist über 14 Perioden, %D als 3er-Durchschnitt) zeigt, wo der Schlusskurs innerhalb der jüngsten Spanne liegt: über 80 überkauft, unter 20 überverkauft. Wie beim RSI gilt: In Trends bleiben Oszillatoren lange im Extrembereich, Signale gegen den Trend sind riskant. Williams %R und CCI messen Ähnliches. Mehrere Oszillatoren gleichzeitig zu nutzen bringt kaum Zusatzinformation, weil sie aus denselben Kursdaten berechnet werden.

## Divergenzen
Klassische (reguläre) Divergenz: Kurs macht ein neues Hoch, RSI/MACD nicht → nachlassendes Momentum, mögliche Umkehr (umgekehrt bei Tiefs). Versteckte Divergenz: Kurs macht ein höheres Tief, der Oszillator ein tieferes Tief → oft Fortsetzung des Aufwärtstrends. Divergenzen können lange bestehen, bevor der Kurs dreht; als Einstieg nur zusammen mit einer Bestätigung (Strukturbruch, Kerzenmuster an einer Zone) nutzen.

## Kerzenmuster
Einzelne Kerzen sind nur im Kontext aussagekräftig – an Unterstützung/Widerstand und in Trendrichtung. Hammer bzw. Pin-Bar (langer unterer Docht, kleiner Körper oben) an einer Unterstützung zeigt abgewiesene Verkäufer; Shooting Star ist das Gegenstück am Widerstand. Bullish/Bearish Engulfing: Der Körper verschluckt die vorherige Kerze komplett. Doji: Unentschlossenheit, wichtig vor allem nach einem langen Trend. Morning/Evening Star: Drei-Kerzen-Umkehrmuster. Immer den Schlusskurs der Kerze abwarten, bevor ein Muster als gültig gilt.

## Chartformationen
Kopf-Schulter (bzw. umgekehrt als Boden): Umkehrformation; das Kursziel ist der Abstand Kopf–Nackenlinie, vom Ausbruchspunkt abgetragen. Doppeltop/-boden: zwei Tests derselben Zone, gültig erst mit Bruch des Zwischentiefs/-hochs. Dreiecke, Flaggen und Wimpel sind meist Fortsetzungsformationen; der Ausbruch sollte mit Volumen kommen. Steigende Keile sind oft bärisch, fallende Keile bullisch. Fehlausbrüche sind häufig – ein Schlusskurs außerhalb der Formation oder ein Retest erhöht die Verlässlichkeit.

## Fibonacci-Retracements
Nach einer Bewegung korrigieren Kurse oft bis zu typischen Anteilen der Strecke: 38,2 %, 50 % und 61,8 % (seltener 23,6 % und 78,6 %). Die Zone 50–61,8 % ist in intakten Trends eine beliebte Einstiegszone für Rücksetzer. Fibonacci-Niveaus sind keine Naturgesetze, sondern wirken, weil viele Marktteilnehmer sie beachten – am stärksten, wenn sie mit einer früheren Unterstützung/Widerstand oder einem gleitenden Durchschnitt zusammenfallen. Erweiterungen (127,2 %, 161,8 %) dienen als Kursziele.

## VWAP und Volumenprofil
Der VWAP (volumengewichteter Durchschnittspreis) beginnt mit jeder Sitzung neu und ist ein Maßstab institutioneller Händler: Kurse über dem VWAP zeigen Käuferkontrolle im Tagesverlauf, darunter Verkäuferkontrolle. Im Intraday-Handel dient er als dynamische Unterstützung bzw. Widerstand. Das Volumenprofil zeigt, auf welchen Preisniveaus am meisten gehandelt wurde (Point of Control); Bereiche mit wenig Volumen werden oft schnell durchlaufen.

## Ichimoku
Tenkan-sen (Mitte der letzten 9 Perioden) und Kijun-sen (26 Perioden) wirken wie schnelle und langsame Durchschnitte. Die Wolke (Senkou Span A und B, 26 Perioden in die Zukunft verschoben) zeigt Trend und Unterstützungszonen: Kurs über der Wolke = Aufwärtstrend, darin = neutral. Die Chikou Span (Schlusskurs 26 Perioden zurückversetzt) bestätigt, wenn sie frei über bzw. unter dem Kurs liegt. Ichimoku funktioniert am besten in klaren Trends und auf höheren Zeitrahmen.

## Liquidität, Spread und Slippage
Spread = Abstand zwischen Geld- und Briefkurs; jeder Market-Trade zahlt ihn. Slippage = Differenz zwischen erwartetem und tatsächlichem Ausführungspreis, besonders bei Nachrichten, dünnen Märkten, außerhalb der Handelszeiten und bei großen Orders. Kleine Altcoins und Nebenwerte haben oft große Spreads – ein Stop von 1 % ist dort kaum sinnvoll. Gebühren und Slippage machen vor allem bei kurzfristigen Strategien mit kleinen Zielen einen großen Teil des Ergebnisses aus.

## Handelszeiten und Sitzungen
Krypto handelt rund um die Uhr. US-Aktien: reguläre Sitzung 15:30–22:00 Uhr deutscher Zeit (in den Wochen der Zeitumstellung eine Stunde abweichend), Xetra 9:00–17:30 Uhr. Devisen handeln werktags rund um die Uhr; am meisten Bewegung gibt es in der London-Sitzung und in der Überschneidung mit New York (ca. 14–17 Uhr deutscher Zeit). Zwischen Sitzungen und über das Wochenende können bei Aktien und Futures Kurslücken (Gaps) entstehen, die Stops überspringen.

## Hebel, Margin und Liquidation
Hebel vergrößert Gewinne und Verluste gleichermaßen. Bei 10-fachem Hebel genügt eine Gegenbewegung von knapp unter 10 % für die Liquidation, weil zusätzlich eine Mindest-Margin gehalten werden muss. Die Positionsgröße sollte sich immer aus dem Risiko bis zum Stop ergeben, nicht aus dem verfügbaren Hebel. Bei unbefristeten Krypto-Futures (Perpetuals) fallen Finanzierungsgebühren (Funding) an, meist alle 8 Stunden: Ist die Funding-Rate positiv, zahlen Long-Positionen an Short-Positionen – stark positive Funding-Raten zeigen überhitzte Long-Positionierung.

## Korrelation und Klumpenrisiko
Positionen, die stark zusammenhängen, sind praktisch eine einzige große Position: mehrere Altcoins (folgen Bitcoin), mehrere US-Tech-Aktien, EUR/USD long und USD/CHF short. Vor einem neuen Trade prüfen, ob er das Gesamtrisiko nur verdoppelt. Faustregel: Das Risiko aller offenen, stark korrelierten Positionen zusammen sollte die Grenze für einen einzelnen Trade nicht weit übersteigen. In Krisen steigen Korrelationen – Diversifikation wirkt dann schwächer als gedacht.

## Kennzahlen eines Handelssystems
Trefferquote, Ø Gewinn/Verlust in R und Erwartungswert sind die Basis. Profit-Faktor = Summe der Gewinne ÷ Summe der Verluste (über 1,5 gilt als solide, unter 1 verliert das System). Maximaler Drawdown = größter Rückgang vom Höchststand – entscheidend für die psychologische Belastbarkeit. Sharpe-Ratio = Rendite pro Einheit Schwankung. Aussagekräftig sind die Zahlen erst ab einigen Dutzend Trades und wenn sie in verschiedenen Marktphasen ähnlich bleiben.

## Trading-Journal
Zu jedem Trade gehören: Grund für den Einstieg, Marktphase, Einstieg/Stop/Ziel, Positionsgröße, Ergebnis in R, Ausstiegsgrund und eine kurze Lektion. Regelmäßig auswerten: Welche Setups verdienen Geld, zu welchen Zeiten, in welcher Marktphase? Wo wurden Regeln gebrochen? Vincent speichert jede Lektion in seinem Gedächtnis und berücksichtigt sie bei künftigen Vorschlägen.

## Paper-Trading in TradingView
TradingView bietet ein eigenes Paper-Trading-Konto mit virtuellem Guthaben. Es ist ideal, um Strategien und die Bedienung risikolos zu testen, ist aber optimistischer als die Realität: Ausführungen erfolgen ohne echte Slippage und Liquiditätsprobleme, und die Psychologie mit echtem Geld fehlt. Paper-Ergebnisse deshalb mit Abschlag bewerten und beim Wechsel auf echtes Geld mit kleiner Größe beginnen. Vor jeder Order muss klar sein, welches Konto in TradingView gerade aktiv ist.
