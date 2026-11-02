# install-tl profile for the pdf-ua-render sidecar. scheme-basic brings the
# LaTeX kernel, tagpdf and LuaTeX; the collections below add fontspec,
# caption, booktabs and luaotfload. Docs and sources are skipped to keep the
# image small; the binaries are linked into /usr/local/bin.
selected_scheme scheme-basic
collection-latexrecommended 1
collection-luatex 1
TEXDIR /usr/local/texlive
TEXMFLOCAL /usr/local/texlive/texmf-local
TEXMFSYSCONFIG /usr/local/texlive/texmf-config
TEXMFSYSVAR /usr/local/texlive/texmf-var
instopt_adjustpath 1
instopt_adjustrepo 0
tlpdbopt_install_docfiles 0
tlpdbopt_install_srcfiles 0
tlpdbopt_autobackup 0
tlpdbopt_sys_bin /usr/local/bin
