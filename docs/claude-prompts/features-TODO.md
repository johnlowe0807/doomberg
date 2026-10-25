1. [x] Remove the volume [v] trigger on the graph screen.
2. [x] FRED /DES screen.
3. [x] Compare graph
	- [x] Cross-type graph comparison (anything graphable)
		- [x] Using non-stock indicators (fred), you use an arg so /COMP AAPL _FRED=[fred index]
	- [x] Compare graph types.
		- [x] Normalized (existing graph)
		- [x] Real (using whatever numbers/scale the graphs use, even if results are funny looking)
		- [x] Stacked (1–8 charted series, each with its own numbered, color-matched scale on the left)
4. [x] Options book commands
	- [x] Just for navigating options, seeing histories, exe...
	- [x] Uses same controls as the graph w/ popup and in-terminal .[COMMAND] controls

Next roadmap: see `features-TODO-pt2.md` §§2–7 for security-type page normalization (`/FUTURES`), data-layer API expansion, queryable fundamental/estimate/holdings fields, remaining data additions, later calendars/news and Yahoo live streaming before any trading capabilities. New notes cover analysis/metric history drill-down, futures-type filtering/sorting, richer visualizations and canonical field/date normalization across provider plugs and security types.
