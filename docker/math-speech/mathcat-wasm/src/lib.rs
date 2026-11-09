//! Thin wasm-bindgen surface over MathCAT for the math-speech sidecar.
//!
//! MathCAT keeps its preferences and the current expression in global state,
//! which is safe here: Node runs each request to completion on one thread.
//! Errors carry a code prefix (`invalid_mathml:` or `speech_failed:`) so the
//! server can tell the caller's input from a MathCAT failure.

use libmathcat::interface::{
    errors_to_string, get_braille, get_spoken_text, get_version, set_mathml, set_preference,
    set_rules_dir,
};
use wasm_bindgen::prelude::*;

const MATHCAT_COMMIT: &str = "95892aba39d1660f41794dd0579f154da960bcf5";

fn failure(code: &str, error: &libmathcat::errors::Error) -> JsError {
    // The first line names the failure; the rest dumps the MathML being spoken,
    // which may quote document content.
    let message = errors_to_string(error);
    let first = message.lines().next().unwrap_or("").trim();
    JsError::new(&format!("{code}: {first}"))
}

/// Points MathCAT at its embedded rules. Call once before anything else.
#[wasm_bindgen]
pub fn init() -> Result<(), JsError> {
    set_rules_dir("Rules").map_err(|e| failure("init_failed", &e))
}

/// MathCAT release and the commit this build pins.
#[wasm_bindgen]
pub fn version() -> String {
    format!("{} ({MATHCAT_COMMIT})", get_version())
}

fn load(mathml: &str, language: &str, style: &str, verbosity: &str) -> Result<(), JsError> {
    for (name, value) in [("Language", language), ("SpeechStyle", style), ("Verbosity", verbosity)] {
        set_preference(name, value).map_err(|e| failure("invalid_preference", &e))?;
    }
    set_mathml(mathml).map_err(|e| failure("invalid_mathml", &e))?;
    Ok(())
}

/// Spoken text of a MathML expression.
#[wasm_bindgen]
pub fn speak(mathml: &str, language: &str, style: &str, verbosity: &str) -> Result<String, JsError> {
    load(mathml, language, style, verbosity)?;
    get_spoken_text().map_err(|e| failure("speech_failed", &e))
}

/// Unicode braille of a MathML expression in the given code (CMU, Nemeth, UEB).
#[wasm_bindgen]
pub fn braille(mathml: &str, code: &str) -> Result<String, JsError> {
    set_preference("BrailleCode", code).map_err(|e| failure("invalid_preference", &e))?;
    set_mathml(mathml).map_err(|e| failure("invalid_mathml", &e))?;
    get_braille("").map_err(|e| failure("braille_failed", &e))
}
