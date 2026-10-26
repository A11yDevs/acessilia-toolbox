-- Alternate text for formulas in the PDF/UA output.
--
-- A caller marks a formula with its spoken form:
--
--   Span ("", ["accessilia-formula"], [("alt", "<text>")]) [Math ...]
--
-- and this filter turns the span into a TeX group that sets the alternate
-- text of the formula it holds (\AccessiliaFormulaAlt, defined in
-- pdf_ua.tex), so the Formula structure element gets /Alt <text> instead of
-- latex-lab's default "LaTeX formula starts ... ends".
--
-- The sidecar rejects raw TeX in the incoming AST; this filter is the only
-- source of raw TeX, and it drops every character that is special to TeX
-- from the text, so the alternate text cannot inject commands or unbalance
-- the group. A span without the class, or with an empty alt, renders its
-- content unchanged.

local FORMULA_CLASS = "accessilia-formula"
local TEX_SPECIALS = "[\\{}$&#^_%%~]"

local function safe_text(text)
  text = text:gsub(TEX_SPECIALS, " "):gsub("%s+", " ")
  return text:match("^%s*(.-)%s*$")
end

function Span(span)
  if not span.classes:includes(FORMULA_CLASS) then
    return nil
  end
  local alt = safe_text(span.attributes["alt"] or "")
  if alt == "" then
    return span.content
  end
  local inlines = { pandoc.RawInline("latex", "{\\AccessiliaFormulaAlt{" .. alt .. "}") }
  for _, inline in ipairs(span.content) do
    table.insert(inlines, inline)
  end
  table.insert(inlines, pandoc.RawInline("latex", "}"))
  return inlines
end
