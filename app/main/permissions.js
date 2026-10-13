'use strict';
/**
 * macOS-Berechtigungen (Mikrofon, Bildschirmaufnahme, Bedienungshilfen): Status abfragen,
 * Mikrofon anfragen und die passenden Systemeinstellungen öffnen.
 * Auf anderen Plattformen liefert alles 'not-applicable'.
 */
const { shell, systemPreferences } = require('electron');

const isMac = process.platform === 'darwin';

const SETTINGS_URLS = {
  microphone: 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone',
  screen: 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture',
  accessibility: 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
};

/**
 * @typedef {'not-determined'|'granted'|'denied'|'restricted'|'unknown'|'not-applicable'} MediaStatus
 * @returns {{microphone: MediaStatus, screen: MediaStatus, accessibility: boolean|'not-applicable'}}
 */
function getStatus() {
  if (!isMac) {
    return { microphone: 'not-applicable', screen: 'not-applicable', accessibility: 'not-applicable' };
  }
  const media = (kind) => {
    try {
      return systemPreferences.getMediaAccessStatus(kind);
    } catch (err) {
      console.error(`[permissions] Status ${kind} nicht lesbar:`, err.message);
      return 'unknown';
    }
  };
  let accessibility = false;
  try {
    accessibility = systemPreferences.isTrustedAccessibilityClient(false);
  } catch (err) {
    console.error('[permissions] Bedienungshilfen-Status nicht lesbar:', err.message);
  }
  return { microphone: media('microphone'), screen: media('screen'), accessibility };
}

/**
 * Fragt den Mikrofon-Zugriff an (zeigt beim ersten Mal den System-Dialog).
 * @returns {Promise<boolean>} true = erlaubt (auf Nicht-Mac immer true)
 */
async function requestMicrophone() {
  if (!isMac) return true;
  try {
    return await systemPreferences.askForMediaAccess('microphone');
  } catch (err) {
    console.error('[permissions] Mikrofon-Anfrage fehlgeschlagen:', err.message);
    return false;
  }
}

/**
 * Öffnet die passende Seite in den Systemeinstellungen.
 * @param {'microphone'|'screen'|'accessibility'} kind
 * @returns {Promise<boolean>} false bei unbekannter Art oder Nicht-Mac
 */
async function openSettings(kind) {
  if (!isMac || typeof kind !== 'string' || !Object.hasOwn(SETTINGS_URLS, kind)) return false;
  const url = SETTINGS_URLS[kind];
  try {
    await shell.openExternal(url);
    return true;
  } catch (err) {
    console.error('[permissions] Systemeinstellungen nicht geöffnet:', err.message);
    return false;
  }
}

module.exports = { getStatus, requestMicrophone, openSettings, SETTINGS_URLS };
