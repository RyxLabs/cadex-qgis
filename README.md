# CADex

[![tests](https://github.com/ryxlabs/cadex-qgis/actions/workflows/tests.yml/badge.svg)](https://github.com/ryxlabs/cadex-qgis/actions/workflows/tests.yml)

**Instrumente pentru cadastrul sistematic și fișierele CGXML, în QGIS.**
**Romanian systematic cadastre and CGXML tools for QGIS.**

[Română](#română) · [English](#english)

---

## Română

CADex este un plugin QGIS pentru lucrările de cadastru sistematic din România. Convertește
livrările CGXML și exporturile eTerra în layere GIS, calculează suprafața fiecărui imobil din
intravilan și din extravilan, reduce proporțional suprafețele din acte la suprafața măsurată
a conturului, compară două versiuni ale acelorași date, verifică topologia sectoarelor
cadastrale și taie construcțiile ieșite din imobil. Layerele și câmpurile sunt preselectate
după numele folosite în firma dumneavoastră. Interfața este în limba română, iar toate
instrumentele funcționează pe o instalare QGIS standard, fără pachete suplimentare.

### În acțiune

**Conversie CGXML.** Alegeți folderul cu livrarea, iar imobilele și construcțiile apar pe
hartă, colorate și etichetate, cu coloanele și numele de câmp alese de dumneavoastră. Același
rezultat se poate scrie și ca Shapefile, DXF sau Excel.

**Verificare Topologică.** Fiecare tip de problemă, de la suprapuneri și goluri la puncte
neracordate și construcții ieșite din imobil, apare într-un layer colorat după tip, iar
butonul *Arată pe hartă* din fereastra de rezultat le încadrează pe hartă.

**Comparator.** Între două livrări, sau între un export eTerra și o livrare, imobilele și
construcțiile apărute, dispărute și modificate apar în layere separate. La cele modificate
vedeți ce coloane s-au schimbat și suprafața cu care s-a mutat limita. Un imobil din eTerra
dezmembrat în livrare se compară cu toate imobilele în care a fost împărțit.

**Raport Intravilan / Extravilan.** Fiecare imobil este colorat după încadrare: integral în
intravilan, parțial sau integral în extravilan. Raportul Excel adaugă suprafețele,
statisticile și proprietarii neidentificați.

**Topologie Construcții.** Construcțiile care ies din imobilul lor mai mult decât permite
toleranța sunt tăiate după limita imobilului, în interiorul lui, într-un layer nou care arată
suprafața pierdută. Când o coloană indică imobilul fiecărei construcții, o construcție aflată
în cea mai mare parte pe alt imobil este doar semnalată.

**Diminuare Suprafețe.** Alegeți tabelul cu parcele și conturul măsurat, iar suprafețele
diminuate apar în fereastră pe măsură ce completați datele. Parcelele cu CF/IE își păstrează
suprafața. Când conturul este mai mare decât suma din acte, suprafețele rămân cele din acte,
iar fereastra arată cu cât depășește conturul suma lor. Rezultatul se salvează în Excel, cu
calculul explicat pe o foaie și parcelele diminuate pe alta.

### Instrumente

Fiecare instrument se pornește din bara CADex, din meniul *Vector > CADex* sau din Processing
Toolbox.

| Instrument | Ce face |
|---|---|
| Conversie CGXML | Citește fișierele `.cgxml` dintr-un folder și din subfolderele lui, dintr-o arhivă ZIP sau alese unul câte unul și scrie un layer de imobile și unul de construcții, ca GeoPackage, Shapefile, DXF cu etichete sau Excel. Alegeți ce coloane se scriu, în ce ordine și cu ce nume de câmp, astfel încât layerele să aibă structura folosită în firma dumneavoastră. În Excel, capul de tabel are denumirea coloanei. |
| Raport Intravilan / Extravilan | Calculează pentru fiecare imobil suprafața din intravilan și din extravilan și încadrarea lui, cu un layer clasificat și un raport Excel cu o foaie pentru toate imobilele, câte o foaie pentru fiecare încadrare, statistici și proprietari neidentificați. Poligoanele de intravilan care se suprapun se numără o singură dată. |
| Diminuare Suprafețe | Reduce proporțional suprafețele din acte ale unei liste de parcele (Excel sau CSV), astfel încât suma lor să fie egală cu suprafața măsurată a conturului. Suprafețele nu se majorează: când conturul este mai mare decât suma din acte, rămân cele din acte, iar rezultatul arată cu cât depășește conturul suma lor. Parcelele care au CF/IE își păstrează suprafața; o celulă CF/IE necompletată sau cu 0, NU, NO, FALSE, FĂRĂ, LIPSĂ, NU ARE, N/A, un semn de întrebare ori o liniuță înseamnă fără CF/IE. Rândurile de total și de subtotal și cele ascunse în Excel se lasă deoparte; o parcelă cu cuvântul total în numele proprietarului sau într-o observație rămâne parcelă. |
| Comparator | Compară două versiuni ale acelorași date și arată ce a apărut, ce a dispărut și ce s-a modificat, inclusiv limitele, pentru imobile și construcții. Versiunile sunt două layere potrivite după o coloană cheie, cu layerele lor de construcții potrivite după imobil și numărul construcției, sau două seturi de fișiere CGXML. |
| Verificare Topologică | Verifică layerele din proiect sau fișierele CGXML pentru un sector complet: imobile fără suprapuneri sau goluri, racordate exact între ele, și fiecare construcție în interiorul imobilului ei. Datele nu se modifică; problemele apar ca layere colorate după tip și, opțional, într-un fișier Excel. |
| Topologie Construcții | Taie înapoi în imobilul pe care se află fiecare construcție din care un punct iese din imobil cu mai mult decât toleranța *Ieșire construcții* sau din care o parte mai lată decât toleranța stă în afara lui (implicit 1 cm, ca în Verificare Topologică). Rezultatul este un layer nou care arată suprafața pierdută. Fiecare parte a unei construcții se taie separat; dintr-o parte împărțită de tăiere se păstrează bucata cea mai mare, iar o parte din care nu rămâne nimic se elimină. Dacă o coloană indică imobilul fiecărei construcții, o construcție aflată pe alt imobil este semnalată, nu tăiată. O construcție cu geometrie invalidă nu se taie și are starea *Eroare*, cu motivul. |

În fiecare instrument, layerele și câmpurile se preselectează după nume. Numele se stabilesc o
singură dată, în *Setări*: fragmente precum `imobil`, `constructi` sau `intravilan`, fără
a ține cont de majuscule sau diacritice, cu `*` în locul oricărui text. Mai multe layere pot
avea același rol, de exemplu imobilele împărțite pe sectoare. Din mai multe conversii ale
acelorași imobile se preselectează doar cea mai nouă, iar sectoarele convertite separat se
preselectează toate. O coloană scrisă cu alte majuscule în fiecare layer, de exemplu `ID` și
`id`, apare o singură dată în liste și se citește din fiecare layer.

Ferestrele instrumentelor nu blochează harta: cu fereastra deschisă puteți selecta elemente
sau examina layerele adăugate de o rulare, apoi puteți modifica o setare și rula din nou. O
fereastră închisă se redeschide așa cum ați lăsat-o, iar fiecare instrument păstrează
setările ultimei rulări și după repornirea QGIS. Căile surselor și ale fișierelor rezultate
și coloanele alese manual se păstrează pentru proiectul în care au fost folosite: în alt
proiect fereastra pornește fără ele, iar layerele și câmpurile se preselectează din nou după
nume. După ce salvați nume noi în *Setări*, o fereastră închisă, în afară de Conversie
CGXML, se redeschide cu setările ultimei rulări, iar layerele și câmpurile se preselectează
după numele noi. Dacă proiectul este închis sau înlocuit în timpul unei rulări, layerele
rezultate nu se adaugă în proiectul deschis între timp; fișierele scrise rămân, iar
fereastra de rezultat arată că proiectul s-a schimbat. La final, fereastra de rezultat are
butoane care deschid fișierul scris (pentru GeoPackage și Shapefile, folderul lui) și
încadrează pe hartă layerele adăugate. Când o rulare adaugă mai multe layere, acestea sunt
puse într-un grup.

### Stereo 70

Toate suprafețele se calculează și toate rezultatele se scriu în Stereo 70 (EPSG:3844),
sistemul de coordonate al fișierelor CGXML. Un layer în alt sistem de coordonate se
reproiectează automat, cu transformările de datum stabilite pentru proiect, iar fereastra de
rezultat arată ce layere au fost reproiectate. EPSG:31700 și definițiile Stereo 70
personalizate se citesc ca EPSG:3844. Un layer fără sistem de coordonate se citește ca
Stereo 70.

Un layer este refuzat înainte de rulare când coordonatele lui nu se potrivesc cu sistemul lui
de coordonate: grade într-un sistem proiectat sau într-un layer fără sistem de coordonate,
metri într-un sistem geografic. Este refuzat și când coordonatele lui, transformate în
Stereo 70, nu se află toate în România. Mesajul cuprinde numele layerului și sistemul lui de
coordonate, iar sistemul se alege din proprietățile layerului.

### Processing Toolbox

Fiecare instrument este și un algoritm din Processing Toolbox, la **CADex**, pentru modele,
rulări în lot, consola Python și `qgis_process`:

| Grup | Algoritm | ID |
|---|---|---|
| Conversie | Conversie CGXML | `cadex:cgxmlconvert` |
| Comparare | Comparator (layere) | `cadex:comparelayers` |
| Comparare | Comparator (fișiere CGXML) | `cadex:comparecgxml` |
| Verificare | Verificare Topologică (layere) | `cadex:topologycheck` |
| Verificare | Verificare Topologică (fișiere CGXML) | `cadex:topologycheckcgxml` |
| Verificare | Topologie Construcții | `cadex:buildingtopology` |
| Analiză | Raport Intravilan / Extravilan | `cadex:intravilanreport` |
| Analiză | Diminuare Suprafețe | `cadex:areareduction` |

Cu aceiași parametri, algoritmii calculează la fel ca ferestrele. Intrările CGXML acceptă
fișiere, arhive ZIP și foldere; conversia scrie coloanele alese în fila *Coloane* a ferestrei
Conversie CGXML. Layerele încărcate în proiect primesc aceeași simbologie ca în ferestre.
Fiecare algoritm primește câte un singur layer pentru fiecare intrare; mai multe layere se
unesc întâi cu `native:mergevectorlayers`. În Comparator (layere), Verificare Topologică
(layere) și Topologie Construcții, pentru layerele create cu Conversie CGXML, coloanele cu
ID-ul imobilului, sectorul cadastral, imobilul construcției și numărul ei se preiau din
conversie când sunt lăsate necompletate, și atunci când un model sau un script dă mai departe
layerele conversiei, temporare sau salvate în fișier. Diminuare Suprafețe citește tabelul cu
numele coloanelor pe primul rând. În panoul de ajutor al fiecărui algoritm sunt descriși
parametrii, valorile implicite, refuzurile și avertismentele lui.

```python
import processing
processing.run("cadex:comparecgxml", {
    "OLD": ["C:/livrari/v1.zip"], "NEW": ["C:/livrari/v2"],
    "OUTPUT_CHANGED": "TEMPORARY_OUTPUT", "OUTPUT_XLSX": "C:/livrari/diferente.xlsx"})
```

### Compararea a două seturi de fișiere CGXML

Fișierele se potrivesc după aceleași reguli ca pe cadex.ro:

- **După numele fișierului.** Un fișier fără pereche după nume se caută din nou după numărul
  cadastral, apoi după IE, astfel încât un fișier redenumit nu apare o dată ca dispărut și o
  dată ca apărut.
- **După identificatorul electronic (IE).** Fișierele se potrivesc după IE, oricum s-ar numi.
  Când una dintre surse este un export eTerra, un IE repetat în cealaltă sursă înseamnă un
  imobil din eTerra dezmembrat, comparat cu toate imobilele care îi păstrează IE-ul, iar un
  imobil fără IE apare doar în sursa lui. Altfel, un fișier fără pereche după IE, de exemplu
  al unui imobil fără IE sau cu un IE pe care îl au mai multe fișiere, se caută apoi după
  numărul cadastral și după nume, deci un set comparat cu el însuși este nemodificat în ambele
  moduri.
- Un export eTerra este recunoscut după sectorul cadastral, necompletat la cele mai multe
  dintre imobilele lui, și poate fi sursa veche sau sursa nouă: rezultatul arată modificările
  de la sursa veche la cea nouă și care dintre surse este exportul. Fișierele unui export
  eTerra au ca nume IE-ul, cele ale unei livrări numărul cadastral, deci la potrivirea după
  nume un fișier din export și unul din livrare sunt același imobil doar când au același IE;
  imobilele din eTerra dezmembrate sunt urmărite doar la potrivirea după IE.
- IE și numărul cadastral sunt unice doar în cadrul unui UAT. Dacă un set conține fișiere din
  mai multe UAT-uri, o cheie se potrivește doar în UAT-ul ei. La o cheie repetată în aceeași
  sursă se păstrează primul fișier, iar celelalte sunt listate, cu motivul. Excepția este IE-ul
  repetat în livrare, la compararea cu un export eTerra, care înseamnă un imobil dezmembrat.
- Un set este refuzat când o arhivă ZIP aleasă nu poate fi deschisă, de exemplu pentru că
  este deteriorată, când un folder ales nu poate fi citit și când o cale aleasă nu există
  sau este un fișier de alt fel: imobilele din partea necitită ar apărea ca apărute sau
  dispărute. Un fișier `.cgxml` care nu poate fi citit este trecut la fișierele ignorate, cu
  motivul, iar imobilul din el, dacă există în celălalt set, apare ca apărut sau dispărut.

Pentru fiecare imobil găsit în ambele seturi se compară coloanele alese, limita și
construcțiile, după numărul lor. Persoanele, înscrierile și părțile a II-a și a III-a ale
cărții funciare se compară pe [cadex.ro/comparator](https://cadex.ro/comparator).

Înainte de comparare, valorile se aduc la aceeași formă:

- Parcelele, tarlalele, categoriile de folosință, titlurile și proprietarii unui imobil se
  compară ca listă, fără ordine: `CC, A` și `A, CC` sunt aceeași valoare. Un număr sau o
  categorie scrise de două ori contează o dată; un nume scris de două ori înseamnă două
  persoane cu același nume.
- Literele ș și ț scrise cu sedilă (`ş`, `ţ`) sunt aceleași litere ca cele scrise cu virgulă,
  cu sau fără opțiunea *Ignoră majusculele și diacriticele*.
- La numerele introduse manual (tarla, parcelă, titlu, numărul cadastral și CF vechi) nu
  contează majusculele și spațiile. Nu contează nici litera dinaintea numărului de tarla
  (`T7`) sau de CF (`CF 4417`), categoria de folosință dinaintea numărului parcelei
  (`A 88/12/3`) și numărul CF electronic scris după cel vechi (`560/N(e:50311)`).
- Semnele puse în locul unui număr lipsă (`-`, `0`, `N/A`) înseamnă valoare necompletată, în
  afară de categoria de folosință, unde `NA` este categoria Nedeterminată.

Un export eTerra și o livrare completează alte coloane. Când exact una dintre cele două surse
este un export eTerra, oricum sunt potrivite fișierele:

- O valoare completată doar într-una din surse nu este o modificare. În exportul eTerra, 0
  la o suprafață sau la numărul de niveluri înseamnă că valoarea lipsește.
- Adresa se compară pe părți: UAT, localitate, stradă, număr. O parte pe care o are doar una
  dintre surse, de exemplu strada, care lipsește din exportul eTerra, sau satul, care lipsește
  din livrare, nu este o modificare, iar `FN` înseamnă fără număr.
- O suprafață care diferă doar prin rotunjirea la metru pătrat nu este o modificare.
- O limită mutată cu cel mult 1 cm nu este o modificare, nici diferența de suprafață măsurată
  care rezultă din ea: eTerra rotunjește coordonatele la milimetru, livrarea nu.
- La un imobil dezmembrat, proprietarii, împrejmuirea și încadrarea în intravilan se iau de la
  toate imobilele în care a fost împărțit: împrejmuirea diferă doar când niciunul nu are
  valoarea din eTerra.
- Coloanele necompletate în tot exportul eTerra, de exemplu sectorul, nu se compară.
- Limita imobilelor și a construcțiilor fără coordonate în exportul eTerra nu se compară.
- Opțiunea *Ignoră majusculele și diacriticele* se preselectează la potrivirea după IE, pentru
  că textele din cele două surse sunt scrise de oameni diferiți. Alegerea se păstrează separat
  pentru două layere și pentru fiecare mod de potrivire a fișierelor.

Fereastra de rezultat și raportul Excel arată câte valori au fost tratate astfel.

Imobilele și construcțiile modificate se împart după ce s-a schimbat: *Doar limita*, *Doar
datele*, *Limita și datele*, la un export eTerra *Dezmembrat*, și *Limita necomparată*, când
datele imobilului sau ale construcției s-au modificat, dar limita nu s-a putut compara,
pentru că lipsesc coordonatele din exportul eTerra sau punctele nu formează o geometrie.
Suprafața măsurată se calculează din limită, deci când limita s-a mutat, o suprafață măsurată
diferită nu se socotește modificare a datelor. Layerul *Modificate* este colorat după aceste
categorii, ca fiecare să poată fi ascunsă din legendă, iar raportul Excel are coloana *Tip
modificare*, pentru filtrare. Fără
compararea geometriei, modificările nu se împart.

Două layere create cu Conversie CGXML se compară cu aceleași reguli pentru valori. Pentru un
export eTerra și o livrare comparați însă fișierele: în layere lipsesc imobilele eTerra fără
coordonate, care ar apărea ca apărute sau dispărute.

La compararea a două layere, două chei care diferă doar prin majuscule sunt aceeași
înregistrare (`13a` și `13A`; zerourile de la început contează). Numerele întregi se compară
exact, deci două coduri lungi care diferă doar prin ultima cifră nu sunt egale. O dată este
aceeași într-o coloană de tip dată și scrisă ca text (`01.03.2024`, `2024-03-01`). Geometria
punctelor și a liniilor se compară după distanță: se consideră mutate când se abat cu peste
1 mm. În Processing, când cheile sunt lăsate necompletate, se folosesc cele pe care le
preselectează fereastra, iar *Compară și coloanele*, nebifat, compară doar geometria.

### Conversia CGXML

Exporturile eTerra și livrările se citesc după aceleași reguli:

- **Persoane.** Toate persoanele înscrise în fișier se preiau. Proprietarii și posesorii, din
  înscrierile de proprietate, de posesie și provizorii, sunt în coloana *Proprietari și
  posesori*. Celelalte persoane sunt în coloana *Alte persoane*, fiecare cu dreptul sau
  înscrierea ei, de exemplu `BANCA EXEMPLU SA (ipotecă)` sau `IONESCU VASILE (notare)`. O
  persoană legată doar de o construcție sau de o unitate individuală, de exemplu un
  apartament, apare și la imobil, cu locul: `(proprietate, C1)`.
- **Găuri și insule.** Punctele din fișier se unesc în contururi închise, în ordinea
  identificatorului lor (POINTID); numărul punctului (NO) este doar numele lui. Un punct scris
  de două ori la rând se ia o singură dată. Un contur aflat în interiorul altuia este o gaură,
  iar un contur aflat într-o gaură este o insulă, adică o parte separată a imobilului. Astfel
  fiecare poligon are forma desenată în fișier.
- **Valori lipsă.** Identificatorii eTerra necompletați rămân necompletați, fără să devină
  zero. Un CADGENNO egal cu `0` este considerat necompletat. Identificatorul unei construcții
  (`100001-C1`) rămâne text.
- **Layere.** Layerele se numesc după sursă, de exemplu *Imobile livrare_v2*, astfel încât
  două conversii din același proiect se deosebesc. Fișierele GeoPackage și Shapefile își
  păstrează simbologia și etichetele și rămân marcate drept conversii pentru celelalte
  instrumente și când le deschideți din nou.
- **Surse.** Dintr-un folder se citesc doar fișierele `.cgxml`; o arhivă ZIP aflată într-un
  folder nu se citește și se alege separat. O arhivă ZIP deteriorată sau blocată, un folder
  care nu poate fi citit, o cale care nu există și un fișier de alt fel sunt semnalate, iar
  celelalte surse se convertesc.
- **Probleme.** În rezultat sunt semnalate: un fișier care nu poate fi citit; o valoare care
  nu este număr într-un câmp numeric, de exemplu sectorul sau o suprafață, care rămâne
  necompletată; o parcelă, o construcție sau un punct fără identificatorul imobilului din
  fișier, citite ca ale lui; un contur a cărui suprafață diferă cu mai mult de 1 mp de
  suprafața măsurată din fișier. Un imobil care apare în mai multe fișiere ale aceluiași UAT,
  după ID sau, fără ID, după IE, este semnalat, pentru că poligonul lui apare de mai multe ori
  în rezultat; același număr în două UAT-uri nu este o dublură. Un fișier care declară un tip
  de document (DOCTYPE) nu se citește.
- **Înregistrări fără geometrie.** Înregistrările fără coordonate în fișier, cele ale căror
  puncte nu închid o suprafață și cele cu o coordonată care nu este număr sau este în afara
  României nu apar pe hartă. Numărul lor, pe fiecare motiv, apare în rezultat, iar formatul
  Excel le cuprinde.
- **Etichete DXF.** Eticheta unui imobil se pune în partea lui neacoperită de construcții,
  astfel încât să nu stea peste etichetele acestora. Textul are cel mult 2,5 m înălțime la
  imobile și cel mult 1,5 m la construcții.

### Verificarea topologică

Valorile implicite sunt alese astfel încât rotunjirea coordonatelor să nu fie raportată:

- **Suprapuneri** între imobile de peste 0,01 mp și mai late de 2 mm. Cele mai mici și cele
  mai înguste provin din rotunjirea coordonatelor: de-a lungul unei limite de 100 m, o fâșie
  de 1 mm are 0,1 mp. O suprapunere cu un drum din sectorul drumurilor este avertisment.
  Sectorul drumurilor este implicit 0; la *Toleranțe* puteți alege alt sector sau niciunul.
- **Goluri** închise între imobile, cu suprafața lor reală: un imobil sau un drum aflat în
  gol se scade din el. Un gol între imobilele unui sector este eroare. Un gol închis între
  imobile din sectoare diferite este avertisment: terenul dintre sectoare poate fi un drum
  care nu se află printre imobilele verificate. Cu limitele sectoarelor se găsesc și terenul
  neacoperit de la marginea sectorului și imobilele care ies din sector. Pragurile sunt
  aceleași ca la suprapuneri.
- **Puncte neracordate**, aflate între 2 mm și 50 cm de limita vecinului, atunci când între ele
  este teren neacoperit sau punctul intră în vecin. Aceste fâșii deschise nu apar la o
  verificare a suprafețelor. Sectorul drumurilor nu se verifică.
- **Construcții**: cele din care un punct iese cu mai mult de 1 cm din imobilul lor; cele din
  care o parte mai lată de 1 cm stă în afara imobilului, chiar fără niciun punct acolo; cele
  care stau în cea mai mare parte pe alt imobil sau nu stau pe niciun imobil; construcțiile
  suprapuse. În fișierele CGXML imobilul construcției este cunoscut; pentru layere se ia
  dintr-o coloană aleasă sau este imobilul pe care construcția îl acoperă cel mai mult.
- **Poligoane invalide**, cu motivul în limba română și locul exact.

Un imobil care apare în mai multe fișiere CGXML, cu același ID în același UAT (sau același
IE când nu are ID) și cu limite care se suprapun pe cel puțin jumătate, se verifică o singură
dată, din fișierul cu cele mai puține subfoldere sub sursa aleasă; fișierele lui sunt listate
în rezultat. Când limitele acestor fișiere sunt în locuri diferite, fiecare fișier se verifică
ca imobil separat, iar fișierele sunt numite în rezultat. Elementele fără geometrie și
înregistrările fără coordonate nu se verifică și sunt numărate în rezultat. O limită al cărei
sector nu apare la niciun imobil cuprinde imobilele al căror sector nu are limită și care stau
în cea mai mare parte în ea; o limită fără niciun imobil nu se verifică. Raportul Excel
cuprinde toleranțele folosite.

### Instalare

În QGIS, deschideți *Plugins > Manage and Install Plugins* și căutați **CADex**.

Instalare din codul sursă:

```bash
cd <profilul QGIS>/python/plugins
git clone https://github.com/ryxlabs/cadex-qgis cadex
```

Apoi activați pluginul în *Plugins > Manage and Install Plugins > Installed*.

### Cerințe

- QGIS 3.34 sau mai nou, inclusiv QGIS 4, cu Qt5 sau Qt6.
- Instrumentele nu au nevoie de pachete suplimentare. Fișierele Excel și desenele DXF se
  scriu, iar fișierele Excel se și citesc, cu ce are orice instalare QGIS.

### Tot de la RyxLabs

[Vernier](https://plugins.qgis.org/plugins/vernier/) este un set de instrumente de editare
vectorială pentru QGIS, util și în lucrările de cadastru: acroșare într-un clic, validator
topologic cu layere de erori, împărțirea unui poligon în suprafețe exacte, import DXF/DWG,
export DXF și KMZ și copii de siguranță automate, cu versiuni.

[CADex Online](https://cadex.ro) este o platformă în care fișierele CGXML se validează, se
compară și se interoghează direct în browser: Validator CGXML, Comparator, Interogări
predefinite și Interogări personalizate. Primele trei sunt gratuite când lucrați cu câte un
fișier.

### Suport

Problemele și propunerile se trimit pe
[pagina de probleme](https://github.com/ryxlabs/cadex-qgis/issues). Pentru întrebări și
suport, scrieți la suport@cadex.ro.

### Licență

GNU General Public License, versiunea 2 sau mai nouă (`GPL-2.0-or-later`), aceeași licență
ca a QGIS. Vedeți [LICENSE](LICENSE).

---

## English

CADex is a QGIS plugin for Romanian systematic cadastre work. It converts CGXML deliveries
and eTerra exports into GIS layers, reports how much of every imobil (a cadastral property,
the unit of a CGXML file) lies inside and outside the intravilan (the built-up area of a
locality), reduces deed areas proportionally to the measured area of a contour, compares two
versions of the same data, checks the topology of cadastral sectors and cuts buildings
(construcții) back inside their imobil. Layers and fields are preselected by the names your
company uses. The interface is in Romanian. Every tool runs on a standard QGIS installation,
with no extra packages.

### In Action

**Conversie CGXML.** You pick the folder with the delivery, and the imobile and buildings
appear on the map, styled and labelled, with the columns and field names you chose. The same
result can also be written as Shapefile, DXF or Excel.

**Verificare Topologică.** Every kind of problem, from overlaps and gaps to unsnapped
points and buildings outside their imobil, is added to a layer styled by type, and the
*Arată pe hartă* button of the result window zooms the map to them.

**Comparator.** Between two deliveries, or between an eTerra export and a delivery, the
imobile and buildings that appeared, disappeared and changed become separate layers. For the
changed ones you see which columns changed and the area by which the boundary moved. An
eTerra imobil split up in the delivery is compared with all the imobile it was split into.

**Raport Intravilan / Extravilan.** Every imobil is coloured by its status: entirely inside
the intravilan, partly inside, or entirely in the extravilan. The Excel report adds the
areas, the statistics and the unidentified owners.

**Topologie Construcții.** Buildings that lie outside their imobil by more than the tolerance
are cut back inside it, into a new layer that records the area removed. When a column holds
each building's imobil, a building standing mostly on another imobil is flagged instead.

**Diminuare Suprafețe.** You pick the parcel table and the measured contour, and the reduced
areas appear in the window as you fill in the inputs. Parcels with a CF/IE (a land book
number or electronic identifier) keep their area. When the contour is larger than the sum of
the deed areas, the areas stay as in the deeds and the window shows by how much the contour
exceeds their sum. The result is saved to Excel, with the calculation explained on one sheet
and the reduced parcels on another.

### Tools

Every tool can be run from the CADex toolbar, from the *Vector > CADex* menu or from the
Processing Toolbox.

| Tool | What it does |
|---|---|
| Conversie CGXML | Reads the `.cgxml` files of a folder and its subfolders, a ZIP archive or a file selection and writes an imobile layer and a construcții layer as GeoPackage, Shapefile, DXF with labels, or Excel. You choose which columns are written, their order and their field names, so the layers match your own structure. Excel headers use the readable column names. |
| Raport Intravilan / Extravilan | For every imobil, the area inside and outside the intravilan and its status (încadrare), as a classified layer and an Excel report with a sheet of all imobile, one sheet per status, statistics and unidentified owners. Overlapping intravilan polygons are counted once. |
| Diminuare Suprafețe | Reduces the deed areas of a parcel list (Excel or CSV) proportionally, so that they add up to the measured contour area. Areas are never increased: when the contour is larger than the sum of the deed areas they stay as in the deeds, and the result shows by how much the contour exceeds their sum. Parcels with a CF/IE keep their area; a CF/IE cell left empty or holding 0, NU, NO, FALSE, FĂRĂ, LIPSĂ, NU ARE, N/A, a question mark or a dash means no CF/IE. Total and subtotal rows and rows hidden in Excel are left out; a parcel with the word total in its owner's name or in a remark stays a parcel. |
| Comparator | Compares two versions of the same data and lists what was added, what was removed and what changed, boundary changes included, for imobile and buildings. The versions are two layers matched on a key column, with their building layers matched on imobil and building number, or two sets of CGXML files. |
| Verificare Topologică | Checks project layers or CGXML files for a complete sector: imobile without overlaps or gaps, snapped to each other, and every building inside its own imobil. The data is not modified; problems are returned as layers styled by type and, optionally, an Excel file. |
| Topologie Construcții | Cuts back inside the imobil it stands on every building with a point more than the *Ieșire construcții* tolerance outside that imobil, or with a part wider than the tolerance outside it (1 cm by default, as in Verificare Topologică). The result is a new layer that records the area removed. Each part of a building is cut on its own; when the cut splits a part, its largest piece is kept, and a part of which nothing is left is removed. When a column holds each building's imobil, a building standing on another imobil is flagged instead of cut. A building with an invalid geometry is not cut and gets the state *Eroare*, with the reason. |

Every tool preselects your layers and fields by name. The names are set once in *Setări*:
fragments such as `imobil`, `constructi` or `intravilan`, matched without regard to case or
diacritics, with `*` as a wildcard. Several layers can have the same role, for example
imobile split by sector. If the same imobile were converted more than once, only the newest
is preselected, while sectors converted one by one are all preselected. A column spelled with a
different case in each layer, such as `ID` and `id`, is listed once and read from every
layer.

The tool windows do not block the map: with a window open you can select features or look at
the layers a run added, then change a setting and run again. A closed window reopens as you
left it, and each tool keeps the settings of its last run across QGIS sessions. The paths of
sources and output files and the columns picked by hand are kept for the project they were
used in: in another project the window starts without them, and its layers and fields are
preselected by name again. After you save new names in *Setări*, a closed window other than
Conversie CGXML instead reopens with the settings of its last run, its layers and fields
preselected by the new names. If the project is closed or replaced while a tool runs, the
result layers are not added to the project open at the end; the files written stay, and the
result window shows that the project changed. At the end of a run,
the result window has buttons that open the file written (for GeoPackage and Shapefile, its
folder) and zoom the map to the layers added. When a run adds several layers, they are put in
one group.

### Stereo 70

Every area is computed and every result is written in Stereo 70 (EPSG:3844), the coordinate
system of CGXML files. A layer in another coordinate system is reprojected on the fly with the
datum transformations set for the project, and the result window lists the layers that were
reprojected. EPSG:31700 and custom Stereo 70 definitions are read as EPSG:3844. A layer
without a coordinate system is read as Stereo 70.

A layer is refused before the run when its coordinates do not fit its coordinate system:
degrees in a projected system or in a layer without a coordinate system, metres in a
geographic system. It is refused as well when its coordinates, transformed to Stereo 70, do
not all lie in Romania. The message names the layer and its coordinate system, which is set
in the layer properties.

### Processing Toolbox

Every tool is also an algorithm in the Processing Toolbox, under **CADex**, for models,
batch processing, the Python console and `qgis_process`:

| Group | Algorithm | ID |
|---|---|---|
| Conversie | Conversie CGXML | `cadex:cgxmlconvert` |
| Comparare | Comparator (layere) | `cadex:comparelayers` |
| Comparare | Comparator (fișiere CGXML) | `cadex:comparecgxml` |
| Verificare | Verificare Topologică (layere) | `cadex:topologycheck` |
| Verificare | Verificare Topologică (fișiere CGXML) | `cadex:topologycheckcgxml` |
| Verificare | Topologie Construcții | `cadex:buildingtopology` |
| Analiză | Raport Intravilan / Extravilan | `cadex:intravilanreport` |
| Analiză | Diminuare Suprafețe | `cadex:areareduction` |

With the same parameters, the algorithms compute the same results as the windows. CGXML
inputs accept files, ZIP archives and folders; the conversion writes the columns chosen in
the *Coloane* tab of the Conversie CGXML window. Layers loaded into the project get the same
styles as in the windows. Each algorithm takes one layer per input; several layers are first
merged with `native:mergevectorlayers`. In Comparator (layere), Verificare Topologică
(layere) and Topologie Construcții, for layers made by Conversie CGXML, the columns holding
the imobil ID, the cadastral sector, a building's imobil and its number are taken from the
conversion when left empty, also when a model or a script passes the conversion's layers on,
temporary or saved to a file. Diminuare Suprafețe reads the table with the column names on
its first row. The help panel of each algorithm lists its parameters, defaults, refusals and
warnings.

```python
import processing
processing.run("cadex:comparecgxml", {
    "OLD": ["C:/livrari/v1.zip"], "NEW": ["C:/livrari/v2"],
    "OUTPUT_CHANGED": "TEMPORARY_OUTPUT", "OUTPUT_XLSX": "C:/livrari/diferente.xlsx"})
```

### Comparing two sets of CGXML files

Files are paired with the same rules as on cadex.ro:

- **By file name** (*După numele fișierului*). A file without a counterpart by name is looked
  up again by cadastral number, then by IE (the electronic identifier), so a renamed file is
  not reported as one removed and one added.
- **By IE** (*După identificatorul electronic*). Files are paired by IE, whatever their names.
  When one source is an eTerra export, an IE repeated in the other source is an eTerra imobil
  that was split up, compared with all the imobile that keep its IE, and an imobil without an
  IE shows up only in its own source. Otherwise a file without a counterpart by IE, such as
  that of an imobil without an IE or with an IE several files hold, is looked up again by
  cadastral number, then by name, so a set compared with itself is unchanged in both modes.
- An eTerra export is recognised by the cadastral sector, empty in most of its imobile, and
  may be the old or the new source: the result shows the changes from the old source to the
  new one and which source is the export. The files of an eTerra export are named by IE,
  those of a delivery by cadastral number, so by file name a file of the export and one of
  the delivery are the same imobil only when they hold the same IE; eTerra imobile that were
  split up are followed only by IE.
- IE and cadastral numbers are unique only within a UAT. When a set holds files of several
  UATs, a key is paired only within its own UAT. For a key repeated within one source the
  first file is kept and the others are listed with the reason, except an IE repeated in the
  delivery when it is compared with an eTerra export, which is a split imobil.
- A set is refused when a chosen ZIP archive cannot be opened, for example because it is
  damaged, when a chosen folder cannot be read, and when a chosen path does not exist or is
  a file of another kind: the imobile of the unread part would show up as added or removed.
  A `.cgxml` file that cannot be read is listed among the ignored files, with the reason,
  and its imobil, when the other set has it, shows up as added or removed.

For every imobil found in both sets, the chosen columns, the boundary and the buildings (by
their number) are compared. Persons, registrations and parts II and III of the land book are
compared on [cadex.ro/comparator](https://cadex.ro/comparator).

Before the comparison, values are brought to one form:

- The parcels, tarlale, land use categories, titles and owners of an imobil are compared as
  lists, regardless of order: `CC, A` and `A, CC` are the same value. A number or a category
  written twice counts once; a name written twice is two persons with that name.
- The letters ș and ț written with a cedilla (`ş`, `ţ`) are the same letters as those with a
  comma below, with or without *Ignoră majusculele și diacriticele*.
- In numbers typed by hand (tarla, parcel, title, old cadastral and CF numbers), case and
  spaces do not count. Nor do the letter before a tarla number (`T7`) or a CF number
  (`CF 4417`), the land use category before a parcel number (`A 88/12/3`) and the electronic CF
  number written after the old one (`560/N(e:50311)`).
- The signs standing for a missing number (`-`, `0`, `N/A`) mean an empty value, except for the
  land use category, where `NA` is the category Nedeterminată (undetermined).

An eTerra export and a delivery fill different columns. When exactly one of the two sources
is an eTerra export, however the files are paired:

- A value only one source has is not a change. In an eTerra export, 0 for an area or for the
  number of levels means the value is missing.
- The address is compared part by part: UAT, locality, street, number. A part only one source
  has, such as the street eTerra does not hold or the village a delivery does not name, is not
  a change, and `FN` means no number.
- An area that differs only by rounding to whole square metres is not a change.
- A boundary that moved by 1 cm or less is not a change, nor is the difference in measured
  area that comes from it: eTerra rounds coordinates to the millimetre, a delivery does not.
- For a split imobil, the owners, the fence and the land class (intravilan) are taken from
  all the imobile it was split into: the fence differs only when none of them holds the
  eTerra value.
- Columns empty in the whole eTerra export, such as the sector, are not compared.
- The boundaries of imobile and buildings without coordinates in the eTerra export are not
  compared.
- *Ignoră majusculele și diacriticele* starts ticked when files are matched by IE, since an
  eTerra export and a delivery are typed by different people. Its state is saved per mode:
  layers, files by name, files by IE.

The result window and the Excel report show how many values were treated this way.

Changed imobile and buildings are split by what changed: *Doar limita* (boundary only),
*Doar datele* (data only), *Limita și datele* (both), for an eTerra export *Dezmembrat*
(split), and *Limita necomparată* (boundary not compared) for an imobil or building whose data
changed and whose boundary could not be compared, because it has no coordinates in the eTerra
export or its points build no geometry. The measured area comes from the boundary, so if
the boundary moved, its change is a boundary change. The *Modificate* layer is styled
by these categories, so each can be hidden from the legend, and the Excel report has a *Tip
modificare* column to filter on. Without the geometry compared, changes are not split.

Two layers made by Conversie CGXML are compared with the same value rules. For an eTerra
export and a delivery, compare the files instead: the layers lack the eTerra imobile without
coordinates, which would show up as added or removed.

When two layers are compared, two keys that differ only in letter case are the same record
(`13a` and `13A`; leading zeros count). Whole numbers are compared exactly, so two long codes
that differ only in their last digit are not equal. A date is the same in a date column and
written as text (`01.03.2024`, `2024-03-01`). The geometry of points and lines is compared by
distance: they count as moved when they lie more than 1 mm away. In Processing, when the key
columns are left empty, the keys the window would preselect are used, and *Compară și
coloanele*, unticked, compares the geometry alone.

### CGXML conversion

eTerra exports and deliveries are read with the same rules:

- **Persons.** Every person registered in the file is read. Owners and possessors, from
  property, possession and provisional registrations, are in the *Proprietari și posesori*
  column. Everyone else is in the *Alte persoane* column, each with their right or
  registration, for example `BANCA EXEMPLU SA (ipotecă)` or `IONESCU VASILE (notare)`. A
  person linked only to a building or to an individual unit, such as an apartment, also
  appears on the imobil, with the place: `(proprietate, C1)`.
- **Holes and islands.** The points in the file are joined into closed outlines, in the
  order of their identifier (POINTID); a point's number (NO) is only its name. A point written
  twice in a row is read once. An outline inside another is a hole, and an outline inside a
  hole is an island, a separate part of the imobil. Each polygon therefore has the shape drawn
  in its file.
- **Missing values.** Empty eTerra identifiers stay empty instead of becoming zeros. A
  CADGENNO of `0` counts as missing. A building identifier (`100001-C1`) stays text.
- **Layers** are named after their source, for example *Imobile livrare_v2*, so two
  conversions in one project are told apart. GeoPackage and Shapefile outputs keep their
  styles and labels, and stay marked as conversions for the other tools when you open them
  again.
- **Sources.** From a folder, only the `.cgxml` files are read; a ZIP archive inside it must be
  chosen separately. A damaged or locked ZIP archive, a folder that cannot be read, a path
  that does not exist and a file of another kind are reported, and the other sources are
  converted.
- **Problems** are reported in the result: a file that cannot be read; a value that is not a
  number in a numeric field, such as the sector or an area, which is left empty; a parcel, a
  building or a point without the identifier of the file's imobil, read as belonging to it;
  an outline whose area differs by more than 1 m² from the measured area in the file. An
  imobil found in several files of the same UAT, by its ID or, without one, by its IE, is
  reported, since its polygon appears more than once in the result; the same number in two
  UATs is not a duplicate. A file that declares a document type (DOCTYPE) is not read.
- **Records without geometry.** Records without coordinates in their file, records whose
  points enclose no area and records with a coordinate that is not a number or lies outside
  Romania are not drawn. Their number, by reason, is shown in the result, and the Excel format
  includes them.
- **DXF labels.** The label of an imobil is placed where none of its construcții stand, so
  that it does not lie on their labels. Text is at most 2.5 m high for imobile and at most
  1.5 m for construcții.

### Topology check

The defaults separate real defects from coordinate rounding:

- **Overlaps** between imobile above 0.01 m² and wider than 2 mm. Smaller and narrower ones
  come from coordinate rounding: along a 100 m boundary, a 1 mm sliver is 0.1 m². An overlap
  with a road of the roads sector is a warning. The roads sector is 0 unless you choose
  another, or none, under *Toleranțe*.
- **Gaps** enclosed by imobile, with their real area: an imobil or road lying inside the gap
  is subtracted from it. A gap between the imobile of one sector is an error. A gap enclosed
  by imobile of different sectors is a warning: the ground between sectors can be a road that
  is not among the checked imobile. With the sector outlines, uncovered ground along a
  sector's edge and imobile that extend outside their sector are found as well. The
  thresholds are the same as for overlaps.
- **Unsnapped points**, 2 mm to 50 cm from a neighbour's boundary, when the ground between
  them is uncovered or the point lies inside the neighbour. Such open slivers do not show up
  in an area check. The roads sector is not checked.
- **Buildings**: those with a point more than 1 cm outside their own imobil; those with a
  part wider than 1 cm outside it, even when no point lies there; those standing mostly on
  another imobil or on none; buildings that overlap each other. In CGXML files the
  building's imobil is known; for layers it comes from a chosen column, or it is the imobil
  the building covers most.
- **Invalid polygons**, with the reason in Romanian and the exact location.

An imobil found in several CGXML files, with the same ID within one UAT (or the same IE when
it has no ID) and outlines that overlap by at least half, is checked once, from the file with
the fewest subfolders below the chosen source; its files are listed in the result. When the
outlines of such files lie in different places, each file is checked as a separate imobil and
the files are named in the result. Features without geometry and records without coordinates
are not checked and are counted in the result. If no imobil has the sector of a sector
outline, the imobile whose own sector has no outline and that lie mostly inside it are
assigned to it; an outline without any imobil is not checked. The Excel report lists the
tolerances used.

### Installation

In QGIS, open *Plugins > Manage and Install Plugins* and search for **CADex**.

To install from source:

```bash
cd <your QGIS profile>/python/plugins
git clone https://github.com/ryxlabs/cadex-qgis cadex
```

Then enable it in *Plugins > Manage and Install Plugins > Installed*.

### Requirements

- QGIS 3.34 or newer, including QGIS 4. Both Qt5 and Qt6 builds are supported.
- No external dependencies. Excel files and DXF drawings are written, and Excel files are
  also read, with what every QGIS installation has.

### Also from RyxLabs

[Vernier](https://plugins.qgis.org/plugins/vernier/) is a vector editing toolkit for QGIS
that is also useful in cadastre work: one-click snapping, a topology validator with error
layers, splitting a polygon into exact target areas, DXF/DWG import, DXF and KMZ export and
versioned automatic backups.

[CADex Online](https://cadex.ro) is a platform where CGXML files are validated, compared and
queried directly in the browser: Validator CGXML, Comparator, Interogări predefinite (predefined
queries) and Interogări personalizate (custom queries). The first three are free for one file
at a time.

### Support

Report bugs and feature requests on the
[issue tracker](https://github.com/ryxlabs/cadex-qgis/issues). For questions and support,
write to suport@cadex.ro.

### License

GNU General Public License, version 2 or later (`GPL-2.0-or-later`), the same license as
QGIS. See [LICENSE](LICENSE).

---

Copyright © 2026 RYXLAB SOFT TECH SRL ([RyxLabs](https://ryxlabs.dev))
