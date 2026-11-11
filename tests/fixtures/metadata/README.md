Mock API answers for tests/test_metadata.py (no test uses the network). Each file follows the documented
response format of its service (field names and nesting as the services publish them); the bibliographic
values are made up or trimmed, so a file is not a byte-for-byte capture of a live answer.

| File | Service and request |
| --- | --- |
| crossref_aps.json | Crossref REST `GET https://api.crossref.org/works/<doi>`, a Physical Review E article with an article number |
| crossref_aiaa_conf.json | Crossref REST, an AIAA conference paper (proceedings-article) with a corporate author |
| crossref_pop.json | Crossref REST, a Physics of Plasmas article (the journal version of arxiv_journal.xml) |
| crossref_search.json | Crossref REST `GET https://api.crossref.org/works?query.bibliographic=...` |
| datacite_csl.json | DOI content negotiation `Accept: application/vnd.citationstyles.csl+json` answered by DataCite |
| arxiv_journal.xml | arXiv API `GET https://export.arxiv.org/api/query?id_list=<id>`, an entry with `arxiv:doi` |
| arxiv_preprint.xml | arXiv API, a preprint without a journal DOI |
| pubmed_esummary.json | NCBI E-utilities `esummary.fcgi?db=pubmed&retmode=json` |
| openlibrary.json | Open Library `GET https://openlibrary.org/api/books?bibkeys=ISBN:<isbn>&format=json&jscmd=data` |
| osti.json | OSTI.GOV `GET https://www.osti.gov/api/v1/records/<id>` |
| ntrs.json | NASA NTRS `GET https://ntrs.nasa.gov/api/citations/<id>` (fields as used by Zotero's NASA NTRS translator) |
| s2.json | Semantic Scholar Graph API `GET /graph/v1/paper/DOI:<doi>` |
| translation_server.json | Zotero translation-server `POST http://127.0.0.1:1969/web` (Zotero API item JSON) |
