# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Help pages of the tools for the help window, and the help of each Processing algorithm."""

from .features import QT_TRANSLATE_NOOP

# tool -> (Processing group, algorithm names)
PROCESSING = {
    "convert_cgxml": (QT_TRANSLATE_NOOP("CADex", "Conversie"),
                      (QT_TRANSLATE_NOOP("CADex", "Conversie CGXML"),)),
    "intravilan_report": (QT_TRANSLATE_NOOP("CADex", "Analiză"),
                          (QT_TRANSLATE_NOOP("CADex", "Raport Intravilan / Extravilan"),)),
    "area_reduction": (QT_TRANSLATE_NOOP("CADex", "Analiză"),
                       (QT_TRANSLATE_NOOP("CADex", "Diminuare Suprafețe"),)),
    "compare_layers": (QT_TRANSLATE_NOOP("CADex", "Comparare"),
                       (QT_TRANSLATE_NOOP("CADex", "Comparator (layere)"),
                        QT_TRANSLATE_NOOP("CADex", "Comparator (fișiere CGXML)"))),
    "check_topology": (QT_TRANSLATE_NOOP("CADex", "Verificare"),
                       (QT_TRANSLATE_NOOP("CADex", "Verificare Topologică (layere)"),
                        QT_TRANSLATE_NOOP("CADex", "Verificare Topologică (fișiere CGXML)"))),
    "building_topology": (QT_TRANSLATE_NOOP("CADex", "Verificare"),
                          (QT_TRANSLATE_NOOP("CADex", "Topologie Construcții"),)),
}

STEREO70 = QT_TRANSLATE_NOOP(
    "CADex",
    "Toate calculele și toate rezultatele sunt în Stereo 70 (EPSG:3844). Un layer în alt "
    "sistem de coordonate se reproiectează automat, iar un layer fără sistem de coordonate "
    "se citește ca Stereo 70. Un layer folosit în calcule este refuzat când coordonatele lui "
    "nu se potrivesc cu sistemul lui de coordonate: grade într-un sistem proiectat sau într-un "
    "layer fără sistem de coordonate, metri într-un sistem geografic. Este refuzat și când "
    "coordonatele lui, transformate în Stereo 70, nu se află toate în România.")

# algorithm name -> the Processing help text, which names the Processing parameters
ALGORITHMS = {
    "cgxmlconvert": QT_TRANSLATE_NOOP("CADex", """
<p>Citește fișierele CGXML și scrie un layer de imobile și, opțional, unul de construcții, un
tabel Excel și un desen DXF. Livrările și exporturile eTerra se citesc după aceleași
reguli.</p>
<h4>Parametri</h4>
<ul>
<li><b>Fișiere CGXML, arhive ZIP sau foldere</b>: oricâte, în orice combinație. Dintr-un
folder se citesc doar fișierele .cgxml; o arhivă ZIP aflată într-un folder nu se citește și
se adaugă separat. Dintr-o arhivă ZIP se citesc toate fișierele .cgxml din ea.</li>
<li><b>Include și subfolderele</b> (implicit bifat): un folder se citește cu subfolderele lui;
nebifat, se citesc doar fișierele aflate direct în el.</li>
<li><b>Imobile</b> și, opțional, <b>Construcții</b>: layerele rezultate, temporare sau salvate
în fișier. Coloanele, ordinea și numele lor sunt cele alese în fila Coloane a ferestrei
Conversie CGXML. Un layer încărcat în proiect primește aceeași simbologie și aceleași etichete
ca în fereastră și numele sursei, de exemplu Imobile livrare_v2. Când nicio construcție nu are
coordonate, layerul de construcții nu se scrie, iar rularea dă un avertisment.</li>
<li><b>Tabel Excel cu toate înregistrările</b> (opțional): foaia Sumar, câte o foaie pentru
imobile și construcții, inclusiv înregistrările fără coordonate, și, când există, foaia
Probleme, cu fișierele necitite și observațiile despre fișiere.</li>
<li><b>Desen DXF cu etichete</b> (opțional): contururile, cu etichetele alese în fila Etichete
DXF a ferestrei.</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>coloanele alese nu pot fi scrise în formatul destinației. În Shapefile și GeoPackage
trebuie bifată cel puțin o coloană, iar numele pot avea doar litere fără diacritice, cifre și
'_' și nu pot începe cu o cifră. În Shapefile numele se scurtează la 10 caractere, deci două
nume care ajung la fel sunt refuzate. GeoPackage nu acceptă numele fid și geom. Un layer temporar
sau un fișier GeoJSON acceptă orice nume, în afară de unul necompletat sau folosit de două
ori. Formatul este cel în care QGIS scrie destinația: și extensiile .shz și .dbf dau un
Shapefile, iar un fișier cu o extensie necunoscută se scrie ca GeoPackage. Coloanele se
verifică înainte de citirea fișierelor;</li>
<li>sursele nu conțin niciun fișier .cgxml sau niciun fișier nu poate fi citit.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Un fișier sau o arhivă ZIP care nu poate fi citită, de exemplu o arhivă deteriorată, o
cale care nu există și un fișier de alt fel apar ca erori în jurnalul rulării, iar celelalte
fișiere se convertesc.</li>
<li>Coloana Proprietari și posesori cuprinde persoanele din înscrierile de proprietate, de
posesie și provizorii. Celelalte persoane din fișier apar în coloana Alte persoane, fiecare
cu dreptul sau înscrierea ei.</li>
<li>Punctele fiecărui imobil se unesc în contururi, în ordinea identificatorului lor
(POINTID): un contur aflat în interiorul altuia este o gaură, iar un contur aflat într-o gaură
este o insulă, adică o parte separată a imobilului.</li>
<li>Un imobil care apare în mai multe fișiere ale aceluiași UAT, după ID sau, fără ID, după
IE, este semnalat într-un avertisment, pentru că poligonul lui apare de mai multe ori în
rezultat. Același număr în două UAT-uri nu este o dublură.</li>
<li>Apar ca avertismente și: o valoare care nu este număr într-un câmp numeric, care rămâne
necompletată; o parcelă, o construcție sau un punct fără identificatorul imobilului din
fișier, citite ca ale lui; un contur a cărui suprafață diferă cu mai mult de 1 mp de suprafața
măsurată din fișier.</li>
<li>Înregistrările fără coordonate în fișier, cele ale căror puncte nu închid o suprafață și
cele cu o coordonată care nu este număr sau este în afara României nu apar în layere. Numărul
lor apare ca avertisment, iar tabelul Excel le cuprinde.</li>
<li>Un poligon invalid se păstrează așa cum este în fișier, cu un avertisment.</li>
<li>Într-un Shapefile, un text mai lung de 254 de octeți se scurtează, iar numărul valorilor
scurtate apare ca avertisment. Coloanele de text au loc și pentru valori completate ulterior
în QGIS: 254 de octeți pentru adresă, persoane și note, 80 pentru denumiri și numere, 20
pentru valorile scurte, cum sunt codurile și numărul poștal; o coloană cu o valoare mai lungă
are lățimea acelei valori. Elementele pe care destinația nu le acceptă sunt numărate într-un
avertisment, cu eroarea primită.</li>
<li>Un tabel Excel sau un desen DXF care nu poate fi scris, de exemplu pentru că este deschis
în alt program, apare ca eroare, iar layerele rămân scrise.</li>
<li>Un layer salvat ca GeoPackage sau Shapefile își păstrează simbologia și rămâne marcat ca
rezultat al conversiei și când îl deschideți din nou. Când layerele conversiei sunt date mai
departe altui algoritm CADex, într-un model sau într-un script, acesta le recunoaște coloanele:
ID-ul imobilului, sectorul și numărul construcției se preiau din conversie.</li>
<li>Coordonatele fișierelor CGXML sunt în Stereo 70 (EPSG:3844), iar layerele și desenul DXF
se scriu în același sistem de coordonate.</li>
<li>Rezultatele numerice sunt Fișiere CGXML găsite, Fișiere necitite, Imobile citite și
Construcții citite.</li>
</ul>
"""),
    "comparelayers": QT_TRANSLATE_NOOP("CADex", """
<p>Compară două versiuni ale acelorași date, ca layere, și scrie înregistrările apărute,
dispărute și modificate. Înregistrările se potrivesc după o coloană cheie.</p>
<h4>Parametri</h4>
<ul>
<li><b>Versiunea veche</b> și <b>Versiunea nouă</b>: cele două layere.</li>
<li><b>Coloana cheie în versiunea veche</b> și <b>Coloana cheie în versiunea nouă</b>
(opțional). Necompletate, cheia este cea preselectată de fereastra Comparatorului: pentru două
layere create cu Conversie CGXML, ID-ul imobilului pentru două livrări care au ID-uri, altfel
IE-ul, iar când unul dintre layere este un export eTerra, tot IE-ul; pentru alte layere,
dintre coloanele numite ca ID-ul sau ca IE-ul imobilului, cea cu mai puține valori
necompletate. O cheie aleasă doar pentru un layer este și a celuilalt, dacă are coloana.</li>
<li><b>Compară și coloanele</b> (implicit bifat). Nebifat, se compară doar geometria.</li>
<li><b>Coloanele de comparat</b> (opțional), din versiunea veche. Necompletate, se compară
toate coloanele pe care le au ambele layere, în afară de cheie și de coloanele care doar
numerotează rândurile, ca fid; pentru două layere create cu Conversie CGXML, coloanele bifate
implicit la compararea fișierelor CGXML.</li>
<li><b>Construcții, versiunea veche</b> și <b>Construcții, versiunea nouă</b> (opțional, ambele
sau niciunul): construcțiile se potrivesc după imobil și numărul lor, de exemplu 1203 C2, cu
<b>Imobilul construcției, versiunea veche</b>, <b>Imobilul construcției, versiunea nouă</b>,
<b>Numărul construcției, versiunea veche</b> și <b>Numărul construcției, versiunea
nouă</b>. Coloana imobilului se alege în ambele layere sau în niciunul; fără ea, construcțiile
se potrivesc doar după număr. Pentru layerele create cu Conversie CGXML, coloana imobilului și
cea a numărului construcției, lăsate necompletate, se preiau din conversie. Când imobilele se
potrivesc după IE, construcțiile se potrivesc după IE-ul imobilului lor, pe care conversia îl
scrie doar dacă este bifat în fila Coloane; fără el, rularea se oprește.</li>
<li><b>Coloanele construcțiilor de comparat</b> (opțional), din construcțiile vechi: necompletate,
coloanele comune, iar pentru layerele create cu Conversie CGXML, cele bifate implicit.</li>
<li><b>Compară și geometria</b> (implicit bifat) și <b>Toleranță pentru geometrie</b>
(implicit 0,1 mp).
O limită se consideră mutată când suprafața dintre conturul vechi și cel nou, sau diferența
dintre suprafețele lor, depășește toleranța, în mp. Suprafața mutată crește cu lungimea
limitei: o limită de 100 m mutată cu 1 mm înseamnă 0,1 mp. Geometria se compară doar când
ambele layere au geometrie. Punctele și liniile se compară după distanță: se consideră mutate
când se abat cu peste 1 mm, iar toleranța în mp nu se folosește.</li>
<li><b>Ignoră majusculele și diacriticele</b> (avansat, implicit nebifat).</li>
<li><b>Apărute</b>, <b>Dispărute</b>, <b>Modificate</b>, <b>Construcții apărute</b>,
<b>Construcții dispărute</b> și <b>Construcții modificate</b>: layerele rezultate, implicit
temporare. Layerele Imobile modificate și Construcții modificate au coloana modificari, cu
coloanele schimbate, și, cu geometria comparată, coloanele sup_mutata, cu suprafața mutată
(la puncte și linii dist_mutata, cu distanța în metri), și tip_modif, cu tipul de
modificare: Doar limita, Doar datele sau Limita și datele. După tip sunt colorate.</li>
<li><b>Raport Excel</b> (opțional).</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>un layer nu are coloana cheie, o coloană de comparat sau o coloană de construcții
aleasă;</li>
<li>cheia este lăsată necompletată și în unul dintre layere nu se găsește după nume coloana
cu ID-ul sau IE-ul imobilului;</li>
<li>nu se compară nici coloanele, nici geometria;</li>
<li>cheia, imobilul sau numărul construcției este o coloană care doar numerotează rândurile,
ca fid;</li>
<li>layerele nu au coloane comune de comparat, iar geometria nu se compară;</li>
<li>este ales un singur layer de construcții, lipsește numărul construcției sau coloana
imobilului este aleasă într-un singur layer de construcții.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Rândurile fără valoare în coloana cheie se ignoră, iar la o cheie repetată se compară
primul rând. Numărul lor apare ca avertisment. Două chei care diferă doar prin majuscule sunt
aceeași înregistrare; zerourile de la început contează.</li>
<li>Numerele întregi se compară exact: două coduri lungi care diferă la ultima cifră sunt
diferite. O dată sau o oră este aceeași într-o coloană de tip dată și scrisă ca text, de
exemplu 01.03.2024 și 2024-03-01. Literele ș și ț sunt aceleași scrise cu virgulă sau cu
sedilă.</li>
<li>Pentru două layere create cu Conversie CGXML, valorile se compară după aceleași reguli ca
fișierele CGXML.</li>
<li>Înainte de comparare, valorile coloanelor CGXML se aduc la aceeași formă. Parcelele,
tarlalele, categoriile de folosință, titlurile și proprietarii unui imobil se compară ca
listă, fără ordine: un număr scris de două ori contează o dată, un nume scris de două ori
înseamnă două persoane. La numerele de tarla, parcelă, titlu, cadastral și CF
vechi nu contează majusculele și spațiile, nici litera dinaintea numărului de tarla (T7) sau
de CF (CF 4417) și nici categoria de folosință dinaintea numărului de parcelă (A 88/12/3).
Semnele puse în locul unui număr lipsă, ca '-', '0' sau 'N/A', înseamnă valoare
necompletată, în afară de categoria de folosință, unde NA este categoria Nedeterminată.</li>
<li>Când unul dintre layere este un export eTerra convertit și celălalt o livrare, rularea dă
un avertisment: pentru un export eTerra și o livrare se compară fișierele CGXML, cu algoritmul
Comparator (fișiere CGXML), pentru că în layere lipsesc imobilele eTerra fără
coordonate.</li>
<li>Rezultatele numerice sunt Apărute, Dispărute, Modificate și Nemodificate, la fel pentru
construcții.</li>
</ul>
"""),
    "comparecgxml": QT_TRANSLATE_NOOP("CADex", """
<p>Compară două seturi de fișiere CGXML, potrivite ca pe cadex.ro, și scrie imobilele și
construcțiile apărute, dispărute și modificate.</p>
<h4>Parametri</h4>
<ul>
<li><b>Sursa veche</b> și <b>Sursa nouă</b>: fișiere .cgxml, arhive ZIP sau foldere; dintr-un
folder se citesc doar fișierele .cgxml. Un export eTerra poate fi oricare dintre surse.</li>
<li><b>Include și subfolderele</b> (implicit bifat).</li>
<li><b>Potrivirea fișierelor</b>. 'După numele fișierului' (implicit): un fișier fără pereche
după nume se caută după numărul cadastral, apoi după IE, astfel încât un fișier redenumit nu
apare o dată ca dispărut și o dată ca apărut. 'După identificatorul electronic (IE)':
fișierele se potrivesc după IE, oricum s-ar numi. Când una dintre surse este un export eTerra,
un imobil din eTerra dezmembrat se compară cu toate imobilele din cealaltă sursă care îi
păstrează IE-ul, iar imobilele fără IE apar doar în sursa lor. Altfel, un fișier fără pereche
după IE se caută apoi după numărul cadastral și după nume.</li>
<li><b>Coloanele imobilului de comparat</b> și <b>Coloanele construcției de comparat</b>:
opțiunile sunt cheile coloanelor din Conversie CGXML, de exemplu sup_mas pentru suprafața
măsurată, sup_acte pentru suprafața din acte și propr pentru proprietari. Implicit sunt
bifate aceleași coloane ca la prima deschidere a ferestrei Comparatorului.</li>
<li><b>Compară și geometria</b> (implicit bifat) și <b>Toleranță pentru geometrie</b>
(implicit 0,1 mp).
O limită se consideră mutată când suprafața dintre conturul vechi și cel nou, sau diferența
dintre suprafețele lor, depășește toleranța, în mp. Suprafața mutată crește cu lungimea
limitei: o limită de 100 m mutată cu 1 mm înseamnă 0,1 mp.
</li>
<li><b>Ignoră majusculele și diacriticele</b> (avansat): Nu, Da sau Doar la potrivirea după
IE (implicit).</li>
<li><b>Compară și construcțiile, după numărul lor</b> (implicit bifat).</li>
<li><b>Imobile apărute</b>, <b>Imobile dispărute</b>, <b>Imobile modificate</b>,
<b>Construcții apărute</b>, <b>Construcții dispărute</b> și <b>Construcții modificate</b>:
layere cu coloanele alese în fila Coloane a ferestrei Conversie CGXML. Layerele Imobile
modificate și Construcții modificate au și coloanele modificari, cu coloanele schimbate, și
sup_mutata, cu suprafața mutată, iar cu
geometria comparată și tip_modif, cu tipul de modificare: Doar limita, Doar datele, Limita și
datele, Dezmembrat sau Limita necomparată. După tip sunt colorate. Construcțiile au și
coloana imobil.</li>
<li><b>Raport Excel</b> (opțional): foaia Sumar și, când există, foile cu imobilele
modificate, apărute și dispărute, cu construcțiile, cu fișierele ignorate și cu observațiile
despre fișiere.</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>o sursă nu conține niciun fișier .cgxml;</li>
<li>o arhivă ZIP aleasă nu poate fi deschisă, de exemplu pentru că este deteriorată, un
folder ales nu poate fi citit, o cale aleasă nu există sau un fișier ales este de alt fel:
imobilele din partea necitită ar apărea ca apărute sau dispărute;</li>
<li>nu este aleasă nicio coloană a imobilului, iar geometria nu se compară.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Înainte de comparare, valorile coloanelor CGXML se aduc la aceeași formă. Parcelele,
tarlalele, categoriile de folosință, titlurile și proprietarii unui imobil se compară ca
listă, fără ordine: un număr scris de două ori contează o dată, un nume scris de două ori
înseamnă două persoane. La numerele de tarla, parcelă, titlu, cadastral și CF
vechi nu contează majusculele și spațiile, nici litera dinaintea numărului de tarla (T7) sau
de CF (CF 4417) și nici categoria de folosință dinaintea numărului de parcelă (A 88/12/3).
Semnele puse în locul unui număr lipsă, ca '-', '0' sau 'N/A', înseamnă valoare
necompletată, în afară de categoria de folosință, unde NA este categoria Nedeterminată.</li>
<li>Un export eTerra și o livrare completează coloane diferite. Când exact una dintre surse
este un export eTerra, oricum sunt potrivite fișierele, o
valoare prezentă doar într-una dintre surse, de exemplu suprafața din acte, împrejmuirea sau
strada din adresă, nu se consideră modificare, iar 0 la o suprafață sau la numărul de niveluri
înseamnă în eTerra că valoarea lipsește. Coloanele necompletate în tot exportul eTerra, de
exemplu sectorul, nu se compară. O suprafață diferită doar prin rotunjirea la metru pătrat și
o limită mutată cu cel mult 1 cm, cu diferența de suprafață măsurată care rezultă din ea, nu
sunt modificări.</li>
<li>Limita imobilelor și a construcțiilor fără coordonate în exportul eTerra nu se compară, la
fel ca limita unui imobil ale cărui puncte nu formează o geometrie. Un astfel de imobil sau o
astfel de construcție cu datele modificate are tipul de modificare 'Limita necomparată'.</li>
<li>IE-ul și numărul cadastral sunt unice doar în cadrul unui UAT, deci o cheie se potrivește
doar în UAT-ul ei. La o cheie repetată în aceeași sursă se păstrează primul fișier, iar
celelalte sunt ignorate, cu motivul, în afară de un IE repetat în livrarea comparată după IE
cu un export eTerra, care înseamnă un imobil dezmembrat.</li>
<li>Un export eTerra este recunoscut după sectorul cadastral, necompletat la cele mai multe
dintre imobilele lui, și poate fi sursa veche sau sursa nouă: rezultatul arată modificările de
la sursa veche la cea nouă și care dintre surse este exportul. Când nicio sursă sau amândouă
sunt exporturi, valorile se compară ca între două livrări.</li>
<li>Fișierele unui export eTerra au ca nume IE-ul, cele ale unei livrări numărul cadastral.
La potrivirea după nume, un fișier din export și unul din livrare sunt același imobil doar
când au același IE, iar imobilele din eTerra dezmembrate sunt urmărite doar la potrivirea după
IE. La un imobil dezmembrat, proprietarii, împrejmuirea și încadrarea în intravilan se iau de
la toate imobilele în care a fost împărțit.</li>
<li>Un fișier .cgxml care nu poate fi citit este trecut la fișierele ignorate, cu motivul,
iar celelalte se compară. Imobilul din el, dacă există în cealaltă sursă, apare ca apărut sau
dispărut.</li>
<li>Înregistrările fără coordonate nu apar în layerele rezultat; numărul lor apare ca
avertisment, iar raportul Excel le cuprinde.</li>
<li>Coordonatele fișierelor CGXML sunt în Stereo 70 (EPSG:3844), ca și layerele
rezultat.</li>
<li>Fișierele ignorate, observațiile despre fișiere și valorile care nu s-au considerat
modificări apar ca avertismente, iar numerele din Sumar ca informații.</li>
<li>Rezultatele numerice sunt Imobile în ambele surse, Imobile modificate, Imobile
nemodificate, Imobile dispărute, Imobile apărute și Fișiere ignorate, iar pentru construcții
cele apărute, dispărute, modificate și nemodificate.</li>
</ul>
"""),
    "topologycheck": QT_TRANSLATE_NOOP("CADex", """
<p>Verifică un layer de imobile și, opțional, unul de construcții: suprapuneri, goluri, puncte
neracordate, construcții ieșite din imobil și poligoane invalide. Algoritmul doar raportează,
nu modifică datele.</p>
<h4>Parametri</h4>
<ul>
<li><b>Imobile</b>: un layer. Mai multe layere de imobile, de exemplu câte unul pe sector, se
unesc întâi într-unul singur cu algoritmul native:mergevectorlayers, care se poate pune în
același model.</li>
<li><b>ID imobil</b> (opțional): coloana după care se numesc imobilele în rezultat și la care
trimite coloana construcțiilor. Necompletată, un imobil se numește după layer și numărul
elementului.</li>
<li><b>Sector cadastral</b> (opțional): un gol închis între imobilele unui sector este
eroare, iar unul închis între imobile din sectoare diferite este avertisment; limitele
sectoarelor se potrivesc după această coloană. Necompletată, toate imobilele formează un
singur sector.</li>
<li><b>Construcții</b> (opțional) și <b>Imobilul construcției</b>, coloana din construcții cu
ID-ul imobilului. Fără ea, o construcție aparține imobilului pe care îl acoperă cel mai mult;
cu ea trebuie aleasă și coloana 'ID imobil'.</li>
<li>Pentru layerele create cu Conversie CGXML, 'ID imobil' și 'Sector cadastral' lăsate
necompletate se preiau din conversie. Când 'ID imobil' este cunoscut, 'Imobilul construcției'
lăsat necompletat se preia din construcțiile create cu Conversie CGXML.</li>
<li><b>Limitele sectoarelor</b> (opțional) și <b>Coloana sectorului în limite</b>: cu ele se
găsesc terenul neacoperit din sector și imobilele care ies din sector. O limită fără sector
ia sectorul cel mai frecvent al imobilelor din interiorul ei. O limită al cărei sector nu
apare la niciun imobil cuprinde imobilele al căror sector nu are limită și care stau în cea
mai mare parte în ea, iar sectorul ei este numit într-un avertisment. O limită fără niciun
imobil nu se verifică.</li>
<li>Parametrii avansați, cu valorile lor implicite: <b>Suprafață minimă a unei suprapuneri sau
a unui gol</b> (0,01 mp); <b>Toleranță de racordare</b> (0,002 m), sub care un punct este pe
limita vecinului, iar o suprapunere sau un gol mai îngust nu se raportează; <b>Distanță de
racordare</b> (0,5 m); <b>Cât poate ieși construcția din imobil</b> (0,01 m);
<b>Sectorul drumurilor dintre sectoare</b> (0). O suprapunere cu un imobil din sectorul
drumurilor este avertisment, iar în el nu se caută nici goluri, nici puncte neracordate; lăsați-l
necompletat dacă nu există.</li>
<li><b>Probleme topologice (suprafețe)</b> și <b>Probleme topologice (puncte)</b>: layerele
cu problemele, colorate după tip, cu coloanele tip, gravitate, sector, obiect, vecin, valoare,
unitate și detalii.</li>
<li><b>Raport Excel</b> (opțional): foaia Sumar, cu toleranțele folosite și numărul
problemelor pe tip și gravitate, și foaia Probleme.</li>
</ul>
<h4>Ce se verifică</h4>
<ul>
<li>Poligoane invalide, cu motivul și locul exact.</li>
<li>Suprapuneri între imobile și goluri închise între imobile. Un gol între imobilele unui
sector este eroare. Un gol închis între imobile din sectoare diferite este avertisment:
terenul dintre sectoare poate fi un drum care nu se află printre imobilele verificate. Cu
limitele sectoarelor, partea unui astfel de gol aflată în interiorul unei limite este
suprafață neacoperită din sector.</li>
<li>Puncte neracordate: un punct aflat la cel mult distanța de racordare de limita unui imobil
vecin, cu teren neacoperit între ele, sau în interiorul vecinului.</li>
<li>Cu limitele sectoarelor: suprafețe neacoperite din sector și imobile în afara
sectorului.</li>
<li>Construcții: cele din care un punct iese din imobilul lor mai mult decât permite
toleranța; cele din care o parte mai lată decât toleranța stă în afara imobilului, chiar fără
niciun punct acolo; cele care stau în cea mai mare parte pe alt imobil decât cel din coloana
lor sau care nu stau pe niciun imobil; construcțiile suprapuse. O valoare din 'Imobilul
construcției' care nu există în imobile este avertisment.</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>layerul de imobile nu are niciun element cu geometrie;</li>
<li>toleranța de racordare nu este mai mică decât distanța de racordare;</li>
<li>'Imobilul construcției' este ales fără 'ID imobil';</li>
<li>o coloană care doar numerotează rândurile, ca fid, este aleasă ca ID, sector, imobil al
construcției sau coloana sectorului în limite.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>O suprapunere sau un gol mai îngust decât toleranța de racordare provine din rotunjirea
coordonatelor și nu se raportează, oricât de lung ar fi. De-a lungul unei limite de 100 m, o
fâșie de 1 mm are 0,1 mp.</li>
<li>Elementele fără geometrie nu se verifică; numărul lor apare ca avertisment.</li>
<li>Un sector al drumurilor ales fără 'Sector cadastral' nu se poate recunoaște: rularea dă un
avertisment, iar drumurile se verifică la fel ca celelalte imobile.</li>
<li>Rezultatele numerice sunt Probleme găsite și Dintre care erori.</li>
</ul>
"""),
    "topologycheckcgxml": QT_TRANSLATE_NOOP("CADex", """
<p>Verifică imobilele și construcțiile din fișiere CGXML: suprapuneri, goluri, puncte
neracordate, construcții ieșite din imobil și poligoane invalide. Algoritmul doar raportează,
nu modifică fișierele.</p>
<h4>Parametri</h4>
<ul>
<li><b>Fișiere CGXML, arhive ZIP sau foldere</b>: dintr-un folder se citesc doar fișierele
.cgxml. Din fiecare fișier se citesc imobilul, ID-ul lui sau, fără ID, IE-ul, sectorul
cadastral și construcțiile lui.</li>
<li><b>Include și subfolderele</b> (implicit bifat).</li>
<li><b>Limitele sectoarelor</b> (opțional) și <b>Coloana sectorului în limite</b>: cu ele se
găsesc terenul neacoperit din sector și imobilele care ies din sector. O limită fără sector
ia sectorul cel mai frecvent al imobilelor din interiorul ei. O limită al cărei sector nu
apare la niciun imobil cuprinde imobilele al căror sector nu are limită și care stau în cea
mai mare parte în ea, iar sectorul ei este numit într-un avertisment. O limită fără niciun
imobil nu se verifică.</li>
<li>Parametrii avansați, cu valorile lor implicite: <b>Suprafață minimă a unei suprapuneri sau
a unui gol</b> (0,01 mp); <b>Toleranță de racordare</b> (0,002 m), sub care un punct este pe
limita vecinului, iar o suprapunere sau un gol mai îngust nu se raportează; <b>Distanță de
racordare</b> (0,5 m); <b>Cât poate ieși construcția din imobil</b> (0,01 m);
<b>Sectorul drumurilor dintre sectoare</b> (0). O suprapunere cu un imobil din sectorul
drumurilor este avertisment, iar în el nu se caută nici goluri, nici puncte neracordate; lăsați-l
necompletat dacă nu există.</li>
<li><b>Probleme topologice (suprafețe)</b> și <b>Probleme topologice (puncte)</b>: layerele
cu problemele, colorate după tip, cu coloanele tip, gravitate, sector, obiect, vecin, valoare,
unitate și detalii.</li>
<li><b>Raport Excel</b> (opțional): foaia Sumar, cu toleranțele folosite și numărul
problemelor pe tip și gravitate, și foaia Probleme.</li>
</ul>
<h4>Ce se verifică</h4>
<ul>
<li>Poligoane invalide, cu motivul și locul exact.</li>
<li>Suprapuneri între imobile și goluri închise între imobile. Un gol între imobilele unui
sector este eroare. Un gol închis între imobile din sectoare diferite este avertisment:
terenul dintre sectoare poate fi un drum care nu se află printre imobilele verificate. Cu
limitele sectoarelor, partea unui astfel de gol aflată în interiorul unei limite este
suprafață neacoperită din sector.</li>
<li>Puncte neracordate: un punct aflat la cel mult distanța de racordare de limita unui imobil
vecin, cu teren neacoperit între ele, sau în interiorul vecinului.</li>
<li>Cu limitele sectoarelor: suprafețe neacoperite din sector și imobile în afara
sectorului.</li>
<li>Construcții: cele din care un punct iese din imobilul lor mai mult decât permite
toleranța; cele din care o parte mai lată decât toleranța stă în afara imobilului, chiar fără
niciun punct acolo; cele care stau în cea mai mare parte pe alt imobil sau care nu stau pe
niciun imobil; construcțiile suprapuse.</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>sursele nu conțin niciun fișier .cgxml sau nicio sursă nu poate fi deschisă, de exemplu
când singura sursă este o arhivă ZIP deteriorată;</li>
<li>niciun fișier nu poate fi citit; mesajul numește primele fișiere și motivul;</li>
<li>niciun imobil din fișierele citite nu are coordonate; mesajul numește și fișierele care
nu au putut fi citite;</li>
<li>toleranța de racordare nu este mai mică decât distanța de racordare;</li>
<li>coloana sectorului în limite doar numerotează rândurile, ca fid.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Un fișier sau o arhivă ZIP care nu poate fi citită apare ca eroare în jurnalul rulării,
iar celelalte fișiere se verifică.</li>
<li>Un imobil care apare în mai multe fișiere, cu același ID în același UAT (sau același IE
când nu are ID) și cu limite care se suprapun pe cel puțin jumătate, se verifică o singură
dată, din fișierul cu cele mai puține subfoldere sub sursa aleasă. Când limitele acestor
fișiere sunt în locuri diferite, fiecare fișier se verifică ca imobil separat. În ambele
cazuri, imobilul este numit într-un avertisment, iar fișierele lui apar ca informații.</li>
<li>Înregistrările fără coordonate nu se verifică; numărul lor apare ca avertisment.</li>
<li>O suprapunere sau un gol mai îngust decât toleranța de racordare provine din rotunjirea
coordonatelor și nu se raportează, oricât de lung ar fi. De-a lungul unei limite de 100 m, o
fâșie de 1 mm are 0,1 mp.</li>
<li>Rezultatele numerice sunt Probleme găsite și Dintre care erori.</li>
</ul>
"""),
    "buildingtopology": QT_TRANSLATE_NOOP("CADex", """
<p>Taie fiecare construcție care iese din imobilul ei mai mult decât permite toleranța,
astfel încât să rămână în interiorul lui, și scrie un layer nou cu suprafața pierdută.
Layerele de intrare nu se modifică.</p>
<h4>Parametri</h4>
<ul>
<li><b>Imobile</b> și <b>Construcții</b>: câte un layer.</li>
<li><b>Imobilul construcției</b> (opțional), coloana din construcții cu ID-ul imobilului, și
<b>ID imobil</b>, coloana din imobile cu aceleași valori. Cu ele, o construcție care stă în
cea mai mare parte pe alt imobil decât al ei are starea 'Pe alt imobil' și nu se taie. Fără
ele, construcția aparține imobilului pe care stă cea mai mare parte din ea. Pentru imobilele
create cu Conversie CGXML, 'ID imobil' lăsat necompletat este coloana cu ID-ul imobilului din
conversie. Când 'ID imobil' este cunoscut, 'Imobilul construcției' lăsat necompletat se preia
din construcțiile create cu Conversie CGXML.</li>
<li><b>Retragere față de limită</b> (implicit 0,002 m): partea care iese din imobil se taie
după limita lui mutată spre interior cu această distanță.</li>
<li><b>Prag de verificare</b> (implicit 1 mp): o construcție care pierde mai mult are starea
'Pierdere peste prag'.</li>
<li><b>Cât poate ieși construcția din imobil</b> (parametru avansat, implicit 0,01 m, ca în
Verificare Topologică): construcția se taie când un punct al ei iese din imobil cu mai mult
de această valoare sau când o parte a ei mai lată decât această valoare stă în afara
imobilului, chiar fără niciun punct acolo. Cu aceeași valoare ca în Verificare Topologică, se
taie exact construcțiile pe care verificarea le raportează ca ieșite din imobil.</li>
<li><b>Coloane preluate din imobil</b> (opțional): se copiază pe fiecare construcție, de la
imobilul ei, cu prefixul 'imb_'.</li>
<li><b>Construcții corectate</b>: toate construcțiile, cu coloanele stare, sup_inainte,
sup_dupa, sup_pierduta și obs_topo, colorate după stare. Un nume pe care layerul îl are deja
primește un sufix, de exemplu stare_2.</li>
</ul>
<h4>Stările</h4>
<ul>
<li>Neschimbată: construcția nu iese din imobil mai mult decât permite toleranța.</li>
<li>Corectată: construcția a fost tăiată și a pierdut cel mult cât pragul.</li>
<li>Pierdere peste prag: construcția a fost tăiată și a pierdut mai mult decât pragul.</li>
<li>Pe alt imobil: construcția stă în cea mai mare parte pe alt imobil decât cel din coloana
ei și nu se taie.</li>
<li>Fără imobil: construcția nu se suprapune cu niciun imobil.</li>
<li>Fără geometrie: construcția se copiază așa cum este.</li>
<li>Eroare: construcția are geometrie invalidă sau tăierea ei nu a reușit; construcția nu se
taie, iar motivul apare în obs_topo.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Fiecare parte a unei construcții din mai multe părți se taie separat. Dintr-o parte pe
care tăierea o împarte în bucăți se păstrează bucata cea mai mare, iar o parte din care nu
rămâne nimic se elimină; ambele sunt notate în obs_topo.</li>
<li>O valoare din 'Imobilul construcției' care nu există în imobile este notată în obs_topo,
iar construcția se taie după imobilul pe care se află.</li>
<li>Rularea se oprește când 'Imobilul construcției' este ales fără 'ID imobil' sau o coloană
care doar numerotează rândurile, ca fid, este aleasă ca legătură.</li>
<li>Rezultatele numerice sunt numărul construcțiilor din fiecare stare.</li>
</ul>
"""),
    "intravilanreport": QT_TRANSLATE_NOOP("CADex", """
<p>Calculează pentru fiecare imobil suprafața din intravilan și pe cea din extravilan și îl
încadrează ca integral în intravilan, parțial în intravilan sau integral în extravilan.</p>
<h4>Parametri</h4>
<ul>
<li><b>Imobile</b>: un layer. Mai multe layere se unesc întâi cu algoritmul
native:mergevectorlayers. Cu opțiunea 'Doar entitățile selectate' a parametrului se
analizează doar imobilele selectate.</li>
<li><b>Contur intravilan</b>: un layer, cu unul sau mai multe poligoane. Poligoanele care se
suprapun se numără o singură dată.</li>
<li><b>ID imobil</b> (opțional): coloana după care se numesc imobilele în clasamentul din
raport; necompletată, numărul elementului.</li>
<li><b>Proprietar, pentru statisticile de proprietari</b> (opțional): raportul numără
imobilele cu proprietar NEIDENTIFICAT și pe cele fără proprietar completat.</li>
<li><b>Toleranță</b> (implicit 0,5 mp): o porțiune de cel mult atât, de o parte sau de alta a
limitei de intravilan, nu schimbă încadrarea imobilului. Când ambele porțiuni sunt în
toleranță, încadrarea o dă porțiunea mai mare.</li>
<li><b>Doar imobilele integral în intravilan</b> (implicit nebifat): layerul rezultat cuprinde
doar imobilele integral în intravilan; raportul Excel le cuprinde pe toate.</li>
<li><b>Raport intravilan</b>: layerul rezultat, cu coloanele imobilelor și sup_totala,
sup_intra, sup_extra, pct_intra și situatie, încadrarea, după care este colorat.</li>
<li><b>Raport Excel</b> (opțional): foaia Toate, câte o foaie pentru fiecare încadrare, foaia
Proprietari neidentificați, când este aleasă coloana proprietarului și există imobile cu
proprietar NEIDENTIFICAT sau necompletat, și foaia Sumar, cu statisticile și cu cele 10
imobile parțiale cu cele mai mari suprafețe în extravilan. Foile cu imobile cuprind toate
coloanele imobilelor, în afară de cele care doar numerotează rândurile.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Suprafețele se rotunjesc la metru pătrat, iar partea din intravilan și cea din extravilan
se rotunjesc astfel încât adunate să dea suprafața totală. Un imobil încadrat integral are
toată suprafața de partea încadrării lui. Totalurile din foaia Sumar se rotunjesc o singură
dată, din suprafețele măsurate, deci pot diferi cu câțiva metri pătrați de suma
rândurilor.</li>
<li>Un imobil cu geometrie invalidă se repară înainte de calcul. Imobilele fără geometrie sau
cu suprafața 0 se ignoră, iar numărul lor apare ca avertisment.</li>
<li>Rularea se oprește când 'ID imobil' sau 'Proprietar' este o coloană care doar numerotează
rândurile, ca fid.</li>
<li>Un raport Excel care nu poate fi scris, de exemplu pentru că este deschis în alt program,
apare ca eroare, iar layerul rămâne scris.</li>
<li>Rezultatele numerice sunt numărul imobilelor integral în intravilan, parțial în
intravilan și integral în extravilan.</li>
</ul>
"""),
    "areareduction": QT_TRANSLATE_NOOP("CADex", """
<p>Reduce proporțional suprafețele din acte ale parcelelor dintr-un tabel, astfel încât suma
lor să fie egală cu suprafața conturului. Parcelele cu CF/IE își păstrează suprafața.</p>
<p>Cu A suma suprafețelor din acte, B suma parcelelor cu CF/IE și C suprafața conturului,
fiecare parcelă fără CF/IE primește suprafața din acte × (C − B) / (A − B). Suprafețele nu
se majorează: când conturul este mai mare decât suma din acte, rămân cele din acte, iar
rezultatul arată cu cât depășește conturul suma lor.</p>
<h4>Parametri</h4>
<ul>
<li><b>Tabelul cu parcele</b>: un layer din proiect sau direct un fișier Excel ori CSV, cu
numele coloanelor pe primul rând. Dintr-un fișier Excel cu mai multe foi, dat direct, se
citește prima foaie. Rândurile de total și de subtotal nu intră în diminuare și
rămân neschimbate. Un rând este de total când o celulă a lui conține doar cuvântul total,
subtotal sau sumă, singur ori într-o denumire ca 'Total general' sau 'Subtotal tarla 12',
oricare i-ar fi suprafața. Când cuvântul apare într-un text mai lung, rândul este de total
doar dacă suprafața lui este suma rândurilor de deasupra ori dacă are doar acel text și
numere, fără datele unei parcele; o parcelă cu un asemenea cuvânt în numele proprietarului
sau într-o observație rămâne parcelă. Dintr-o foaie .xlsx, rândurile ascunse în Excel se
lasă deoparte și nu apar în rezultat.</li>
<li><b>Coloana suprafeței din acte</b>.</li>
<li><b>Coloana CF/IE</b> (opțional): parcelele care au o valoare în ea își păstrează
suprafața. O celulă necompletată sau cu 0, NU, NO, FALSE, FĂRĂ, LIPSĂ, NU ARE, N/A, un semn
de întrebare ori o liniuță înseamnă fără CF/IE.</li>
<li><b>Layer contur</b> (opțional): suprafața conturului se calculează din geometria lui. Un
layer cu mai multe elemente se folosește doar cu opțiunea 'Doar entitățile selectate' a
parametrului, iar atunci se adună suprafețele elementelor selectate.</li>
<li><b>Suprafața conturului</b> (mp), folosită când nu este ales un layer contur.</li>
<li><b>Rotunjire</b>: la metru pătrat (implicit) sau la două zecimale. Când conturul nu
depășește suma din acte, suma tuturor parcelelor este egală cu suprafața conturului rotunjită
la fel, în afară de două cazuri, în care mesajele rulării și foaia Sumar arată suma
obținută: parcelele cu CF/IE au mai multe zecimale decât rotunjirea, ori rotunjirea în sus ar
da unei parcele o suprafață mai mare decât cea din acte.</li>
<li><b>Parcele diminuate</b>: tabelul cu coloanele sup_diminuata și diferenta adăugate. Un
tabel cu geometrie își păstrează geometria, în Stereo 70.</li>
<li><b>Raport Excel</b> (opțional): foaia Sumar, cu tabelul, conturul și calculul, și foaia
Diminuare, cu tabelul și cele două coloane noi.</li>
</ul>
<h4>Rularea se oprește când</h4>
<ul>
<li>nu este ales nici un layer contur, nici o suprafață a conturului;</li>
<li>geometria layerului contur are suprafața 0;</li>
<li>layerul contur are mai multe elemente și opțiunea 'Doar entitățile selectate' nu este
bifată, ori este bifată, dar niciun element nu este selectat;</li>
<li>coloana aleasă nu conține nicio suprafață numerică sau suma suprafețelor din acte este
0;</li>
<li>conturul nu depășește suma din acte, dar diminuarea nu este posibilă: toate parcelele au
CF/IE, parcelele fără CF/IE au împreună cel mult 1 mp sau conturul nu depășește suma
parcelelor cu CF/IE;</li>
<li>o coloană care doar numerotează rândurile, ca fid, este aleasă ca suprafață sau CF/IE;</li>
<li>tabelul nu are coloana aleasă ca suprafață sau CF/IE, de exemplu când numele coloanelor
nu sunt pe primul lui rând; mesajul numește coloanele tabelului.</li>
</ul>
<h4>Bine de știut</h4>
<ul>
<li>Rândurile de total, cele fără o suprafață numerică și cele cu suprafața negativă rămân
neschimbate în rezultat, iar rândurile ascunse în Excel lipsesc din el. Toate apar în
avertismente, rândurile de total cu motivul fiecăruia, iar rândurile de total și cele
ascunse sunt numite și în foaia Sumar. Un rând cu cuvântul 'total' sau 'sumă' care rămâne
parcelă apare tot ca avertisment.</li>
<li>Rezultatele numerice sunt Factor, (C − B) / (A − B), Diminuare (%), cât pierde fiecare
parcelă fără CF/IE, și, în mp, cu cât depășește conturul suma din acte. Când conturul este
mai mare decât suma din acte, Factor este 1, iar Diminuare este 0.</li>
</ul>
"""),
}

TOOLS = {
    "convert_cgxml": QT_TRANSLATE_NOOP("CADex", """
<p>Citește fișierele CGXML și scrie un layer de imobile și unul de construcții. Livrările și
exporturile eTerra se citesc după aceleași reguli.</p>
<h4>Ce alegeți</h4>
<ul>
<li><b>Sursa:</b> un folder, cu sau fără subfolderele lui, o arhivă ZIP sau unul ori mai multe
fișiere .cgxml. Le puteți și trage în câmp din Explorer. Dintr-un folder se citesc doar
fișierele .cgxml; o arhivă ZIP aflată într-un folder nu se citește și se alege separat.</li>
<li><b>Formatul:</b> GeoPackage, cu ambele layere într-un fișier, Shapefile, câte un fișier pe
layer, DXF cu etichete sau Excel, cu toate înregistrările, inclusiv cele fără coordonate.</li>
<li><b>Coloanele:</b> în fila Coloane alegeți ce coloane se scriu, în ce ordine și cu ce nume
de câmp, ca layerele să aibă structura folosită în firma dumneavoastră. În Excel, capul de
tabel are denumirea coloanei. În GeoPackage și Shapefile, numele de câmp pot avea doar litere
fără diacritice, cifre și '_'; în Shapefile se scurtează la 10 caractere, iar GeoPackage nu
acceptă numele fid și geom.</li>
</ul>
<h4>Ce obțineți</h4>
<p>Fișierele rezultate și, dacă doriți, layerele adăugate în proiect cu simbologie și
etichete, numite după sursă, de exemplu Imobile livrare_v2. Fișierele GeoPackage și Shapefile
își păstrează simbologia și rămân marcate ca rezultate ale conversiei și când le deschideți
din nou. La final vedeți câte imobile și construcții s-au citit, ce fișiere nu au putut fi
citite și câte înregistrări nu apar pe hartă, după motiv.</p>
<h4>Bine de știut</h4>
<ul>
<li>O arhivă ZIP deteriorată sau blocată, un folder care nu poate fi citit și un fișier de
alt fel apar în linia de stare de sub sursă, iar restul surselor se convertește. O sursă în
care nu se găsește niciun fișier .cgxml este refuzată înainte de rulare, iar dacă niciun
fișier nu poate fi citit, nu se scrie niciun rezultat.</li>
<li>Coloana Proprietari și posesori cuprinde persoanele din înscrierile de proprietate, de
posesie și provizorii. Toate celelalte persoane din fișier, de exemplu un creditor ipotecar
sau o persoană dintr-o notare, apar în coloana Alte persoane, fiecare cu dreptul sau
înscrierea ei.</li>
<li>Punctele fiecărui imobil se unesc în contururi, în ordinea identificatorului lor
(POINTID): un contur aflat în interiorul altuia este o gaură, iar un contur aflat într-o gaură
este o insulă, adică o parte separată a imobilului.</li>
<li>Înregistrările fără coordonate în fișier, cele ale căror puncte nu închid o suprafață și
cele cu o coordonată care nu este număr sau este în afara României nu apar pe hartă; formatul
Excel le cuprinde.</li>
<li>În DXF, eticheta unui imobil se pune în partea lui neacoperită de construcții, astfel încât
să nu stea peste etichetele acestora. Textul etichetelor are cel mult 2,5 m înălțime la imobile
și cel mult 1,5 m la construcții.</li>
<li>În detaliile rezultatului apar și: o valoare care nu este număr într-un câmp numeric, care
rămâne necompletată; o parcelă, o construcție sau un punct fără identificatorul imobilului din
fișier, citite ca ale lui; un contur a cărui suprafață diferă cu mai mult de 1 mp de suprafața
măsurată din fișier.</li>
<li>Un imobil care apare în mai multe fișiere ale aceluiași UAT, după ID sau, fără ID, după
IE, este semnalat, pentru că poligonul lui apare de mai multe ori în rezultat. Același număr
în două UAT-uri nu este o dublură.</li>
<li>O conversie anulată sau nereușită lasă neschimbate fișierele rezultate anterior, iar
layerele lor, eliminate din proiect la pornirea conversiei, sunt adăugate la loc. O conversie
nouă înlocuiește toate fișierele unui Shapefile cu același nume, inclusiv indexul
spațial.</li>
</ul>
"""),
    "intravilan_report": QT_TRANSLATE_NOOP("CADex", """
<p>Calculează pentru fiecare imobil suprafața din intravilan și pe cea din extravilan și îl
încadrează ca integral în intravilan, parțial în intravilan sau integral în extravilan.</p>
<h4>Ce alegeți</h4>
<ul>
<li><b>Layerele de imobile:</b> unul sau mai multe, de exemplu câte unul pe sector. Cu
'Doar imobilele selectate' se analizează doar selecția de pe hartă.</li>
<li><b>Layerele de contur intravilan:</b> unul sau mai multe layere cu conturul
intravilanului.</li>
<li><b>ID imobil</b>, după care se numesc imobilele în clasamentul din raport, și, opțional,
<b>Proprietar</b>, pentru statisticile de proprietari neidentificați.</li>
<li><b>Toleranța:</b> o porțiune de cel mult atât, de o parte sau de alta a limitei, nu
schimbă încadrarea imobilului. Când ambele porțiuni sunt în toleranță, încadrarea o dă
porțiunea mai mare.</li>
<li><b>Rezultatul:</b> un layer GeoPackage sau, fără el, un layer temporar, opțional doar cu
imobilele integral în intravilan, și un raport Excel opțional, cu coloanele imobilelor
bifate la 'Coloane în Excel'.</li>
</ul>
<h4>Ce obțineți</h4>
<p>Un layer colorat după încadrare, cu suprafețele calculate și coloana situatie, și,
opțional, un raport Excel cu foaia Toate, câte o foaie pentru fiecare încadrare, foaia
Proprietari neidentificați, când există, și foaia Sumar, cu statisticile și cu cele 10
imobile parțiale cu cele mai mari suprafețe în extravilan.</p>
<h4>Bine de știut</h4>
<ul>
<li>Poligoanele de intravilan care se suprapun se numără o singură dată.</li>
<li>Suprafețele se rotunjesc la metru pătrat, iar partea din intravilan și cea din extravilan
se rotunjesc astfel încât adunate să dea suprafața totală. Un imobil încadrat integral are
toată suprafața de partea încadrării lui. Totalurile din fereastra de rezultat și din foaia
Sumar se rotunjesc o singură dată, din suprafețele măsurate, deci pot diferi cu câțiva metri
pătrați de suma rândurilor.</li>
<li>O coloană scrisă cu alte majuscule în fiecare layer de imobile, de exemplu ID și id, se
citește ca aceeași coloană.</li>
<li>Într-un GeoPackage existent, raportul se scrie ca layerul 'raport_intravilan', în locul
celui cu același nume; celelalte layere din fișier rămân neschimbate.</li>
</ul>
"""),
    "area_reduction": QT_TRANSLATE_NOOP("CADex", """
<p>Reduce proporțional suprafețele din acte ale unei liste de parcele, astfel încât suma lor să
fie egală cu suprafața măsurată a conturului.</p>
<p>Cu A suma suprafețelor din acte, B suma parcelelor cu CF/IE și C suprafața conturului,
fiecare parcelă fără CF/IE primește suprafața din acte × (C − B) / (A − B). Suprafețele nu
se majorează: când conturul este mai mare decât suma din acte, rămân cele din acte, iar
fereastra arată cu cât depășește conturul suma lor.</p>
<h4>Ce alegeți</h4>
<ul>
<li><b>Tabelul</b> Excel sau CSV cu parcelele și foaia lui; la deschidere se citește prima
foaie care se vede în Excel. Numele coloanelor se iau din primul rând cu majoritatea
celulelor completate, deci un titlu deasupra tabelului se sare. Rândurile
de total și de subtotal și rândurile ascunse în Excel se lasă deoparte, iar fereastra arată
care sunt. Un rând este de total când o celulă a lui conține doar cuvântul total, subtotal
sau sumă, singur ori într-o denumire ca 'Total general' sau 'Subtotal tarla 12', oricare
i-ar fi suprafața. Când cuvântul apare într-un text mai lung, rândul este de total doar dacă
suprafața lui este suma rândurilor de deasupra ori dacă are doar acel text și numere, fără
datele unei parcele; o parcelă cu un asemenea cuvânt în numele proprietarului sau într-o
observație rămâne parcelă, iar fereastra o numește.</li>
<li><b>Coloana suprafeței din acte</b> și, opțional, <b>coloana CF/IE</b>. Parcelele care au o
valoare în ea își păstrează suprafața; o celulă necompletată sau cu 0, NU, NO, FALSE, FĂRĂ,
LIPSĂ, NU ARE, N/A, un semn de întrebare ori o liniuță înseamnă fără CF/IE.</li>
<li><b>Suprafața conturului:</b> preluată din geometria unui layer, doar din elementele
selectate dacă doriți, sau introdusă manual.</li>
<li><b>Rotunjirea:</b> la metru pătrat sau la două zecimale. Când conturul nu depășește suma
din acte, suma tuturor parcelelor este egală cu suprafața conturului rotunjită la fel, în
afară de două cazuri, în care fereastra și foaia Sumar arată suma obținută: parcelele cu
CF/IE au mai multe zecimale decât rotunjirea, ori rotunjirea în sus ar da unei parcele o
suprafață mai mare decât cea din acte.</li>
</ul>
<h4>Ce obțineți</h4>
<p>Rezultatul apare în fereastră imediat ce datele sunt complete. 'Salvează Excel' scrie
tabelul inițial cu suprafața diminuată și diferența adăugate și o foaie Sumar cu
calculul.</p>
<h4>Bine de știut</h4>
<ul>
<li>Rândurile fără o suprafață numerică și cele cu suprafața negativă rămân neschimbate și
sunt colorate în tabel.</li>
</ul>
"""),
    "compare_layers": QT_TRANSLATE_NOOP("CADex", """
<p>Compară două versiuni ale acelorași date și arată ce a apărut, ce a dispărut și ce s-a
modificat. Alegeți între două layere și două seturi de fișiere CGXML.</p>
<h4>Două layere</h4>
<p>Înregistrările se potrivesc după coloana cheie a fiecărui layer. Pentru layerele create cu
Conversie CGXML, cheia se preselectează: ID-ul imobilului pentru două livrări, IE-ul când unul
dintre layere este un export eTerra. Pentru alte layere, dintre coloanele numite ca ID-ul sau
ca IE-ul imobilului după numele din Setări, se preselectează cea cu mai puține valori
necompletate. Două chei care diferă doar prin majuscule sunt aceeași înregistrare. Se compară
coloanele bifate și, opțional, geometria; punctele și liniile se compară după distanță și se
consideră mutate când se abat cu peste 1 mm. Bifați secțiunea
'Construcții' pentru a compara și două layere de construcții, potrivite după imobil și
numărul lor, de exemplu 1203 C2. Pentru layerele create cu Conversie CGXML, layerele de
construcții și coloanele lor se preselectează. La deschidere se preselectează cea mai nouă
conversie CGXML și, ca versiune veche, cea mai apropiată conversie anterioară a acelorași
date, de preferință una în care imobilele diferă.</p>
<h4>Două seturi de fișiere CGXML</h4>
<p>Fiecare set poate fi un folder, o arhivă ZIP sau fișiere alese unul câte unul. Fișierele se
potrivesc la fel ca pe cadex.ro, după numele fișierului sau după IE, cum alegeți la
'Potrivirea fișierelor'. La potrivirea după nume, un fișier redenumit se regăsește după
numărul cadastral sau după IE. La potrivirea după IE, când nicio sursă nu este un export
eTerra, un fișier fără pereche se caută apoi după numărul cadastral și după nume. Un export
eTerra poate fi oricare dintre surse. Pentru fiecare imobil găsit
în ambele seturi se compară coloanele bifate, limita și, opțional, construcțiile. Coloanele
imobilelor și ale construcțiilor se bifează separat, pe cele două file. Un set este refuzat
când o arhivă ZIP aleasă nu poate fi deschisă, de exemplu pentru că este deteriorată, când un
folder ales nu poate fi citit și când o cale aleasă nu există sau este un fișier de alt fel:
imobilele din partea necitită ar apărea ca apărute sau dispărute. Un fișier .cgxml care nu
poate fi citit este trecut la fișierele ignorate, cu
motivul, iar imobilul din el, dacă există în celălalt set, apare ca apărut sau
dispărut.</p>
<h4>Ce obțineți</h4>
<p>Layere temporare cu înregistrările apărute, dispărute și modificate, strânse într-un grup.
Layerul 'Modificate' arată ce coloane s-au schimbat și suprafața mutată și, cu geometria
comparată, este colorat după ce s-a schimbat: doar limita, doar datele, limita și datele sau,
la un export eTerra, un imobil dezmembrat. La fișierele CGXML, 'Limita necomparată' înseamnă
că datele imobilului sau ale construcției s-au modificat, dar limita nu s-a putut compara.
Fiecare categorie se poate ascunde din legendă. Numărul înregistrărilor nemodificate apare în
fereastra de rezultat. Raportul Excel este opțional.</p>
<h4>Bine de știut</h4>
<ul>
<li>O limită se consideră mutată când suprafața dintre conturul vechi și cel nou, sau diferența
dintre suprafețele lor, depășește toleranța, în mp. Suprafața mutată crește cu lungimea
limitei: o limită de 100 m mutată cu 1 mm înseamnă 0,1 mp.
</li>
<li>Un imobil din exportul eTerra dezmembrat în livrare, în mai multe imobile care îi păstrează
IE-ul, se compară cu toate imobilele din livrare la un loc.</li>
<li>Înainte de comparare, valorile coloanelor CGXML se aduc la aceeași formă. Parcelele,
tarlalele, categoriile de folosință, titlurile și proprietarii unui imobil se compară ca
listă, fără ordine: un număr scris de două ori contează o dată, un nume scris de două ori
înseamnă două persoane. La numerele de tarla, parcelă, titlu, cadastral și CF
vechi nu contează majusculele și spațiile, nici litera dinaintea numărului de tarla (T7) sau
de CF (CF 4417) și nici categoria de folosință dinaintea numărului de parcelă (A 88/12/3).
Semnele puse în locul unui număr lipsă, ca '-', '0' sau 'N/A', înseamnă valoare
necompletată, în afară de categoria de folosință, unde NA este categoria Nedeterminată.</li>
<li>Un export eTerra și o livrare completează coloane diferite. Când exact una dintre surse
este un export eTerra, oricum sunt potrivite fișierele, o
valoare prezentă doar într-una dintre surse, de exemplu suprafața din acte, împrejmuirea sau
strada din adresă, nu se consideră modificare, iar 0 la o suprafață sau la numărul de niveluri
înseamnă în eTerra că valoarea lipsește. Coloanele necompletate în tot exportul eTerra, de
exemplu sectorul, nu se compară. O suprafață diferită doar prin rotunjirea la metru pătrat și
o limită mutată cu cel mult 1 cm, cu diferența de suprafață măsurată care rezultă din ea, nu
sunt modificări. Numărul
valorilor care nu s-au considerat modificări apare în fereastra de rezultat și în raportul
Excel.</li>
<li>Opțiunea 'Ignoră majusculele și diacriticele' se preselectează la potrivirea după IE.
Literele ș și ț sunt aceleași scrise cu virgulă sau cu sedilă, cu sau fără această opțiune.</li>
<li>Limita imobilelor și a construcțiilor fără coordonate în exportul eTerra nu se compară, la
fel ca limita unui imobil ale cărui puncte nu formează o geometrie. Un astfel de imobil sau o
astfel de construcție cu datele modificate are tipul de modificare 'Limita necomparată'.</li>
<li>Un export eTerra este recunoscut după sectorul cadastral, necompletat la cele mai multe
dintre imobilele lui, și poate fi sursa veche sau sursa nouă: rezultatul arată modificările de
la sursa veche la cea nouă și care dintre surse este exportul. Când nicio sursă sau amândouă
sunt exporturi, valorile se compară ca între două livrări.</li>
<li>Fișierele unui export eTerra au ca nume IE-ul, cele ale unei livrări numărul cadastral.
La potrivirea după nume, un fișier din export și unul din livrare sunt același imobil doar
când au același IE, iar imobilele din eTerra dezmembrate sunt urmărite doar la potrivirea după
IE. La un imobil dezmembrat, proprietarii, împrejmuirea și încadrarea în intravilan se iau de
la toate imobilele în care a fost împărțit.</li>
<li>Suprafața măsurată se calculează din limită: dacă limita s-a mutat, o suprafață măsurată
diferită nu se socotește modificare a datelor.</li>
<li>Pentru un export eTerra și o livrare comparați fișierele CGXML, nu layerele convertite: în
layere lipsesc imobilele eTerra fără coordonate.</li>
<li>Comparația completă, cu persoane, înscrieri și părțile a II-a și a III-a ale cărții
funciare, se face pe cadex.ro/comparator.</li>
</ul>
"""),
    "check_topology": QT_TRANSLATE_NOOP("CADex", """
<p>Verifică dacă imobilele acoperă sectorul cadastral fără suprapuneri sau goluri și se
racordează exact și dacă fiecare construcție stă în interiorul imobilului ei. Instrumentul doar
raportează, nu modifică nimic.</p>
<h4>Ce alegeți</h4>
<ul>
<li><b>Layerele din proiect</b>, cu coloanele 'ID imobil', 'Sector cadastral' și 'Imobilul
construcției', care pentru layerele create cu Conversie CGXML se preselectează. Fără sector,
toate imobilele formează un singur sector; fără 'Imobilul construcției', o construcție
aparține imobilului pe care îl acoperă cel mai mult.</li>
<li>Sau <b>fișiere CGXML</b>: un folder, o arhivă ZIP sau fișiere .cgxml, din care se citesc
imobilul, sectorul și construcțiile fiecărui fișier.</li>
<li>Opțional, <b>limitele sectoarelor</b>, pentru terenul neacoperit de la marginea
sectorului și imobilele care ies din sector.</li>
<li><b>Toleranțele</b>, cu valorile implicite de mai jos, și sectorul drumurilor (implicit
0).</li>
</ul>
<h4>Ce se verifică</h4>
<ul>
<li><b>Suprapuneri</b> între imobile peste 0,01 mp. Una cu un imobil din sectorul drumurilor
este avertisment.</li>
<li><b>Goluri</b> închise între imobile, cu suprafața lor reală. Un gol între imobilele unui
sector este eroare. Un gol închis între imobile din sectoare diferite este avertisment:
terenul dintre sectoare poate fi un drum care nu se află printre imobilele verificate. Cu
limitele sectoarelor se găsesc și terenul neacoperit de la marginea sectorului și imobilele
care ies din sector.</li>
<li><b>Puncte neracordate</b>, între 2 mm și 50 cm de limita vecinului, cu teren neacoperit
între ele sau în interiorul vecinului. Sectorul drumurilor nu se verifică la goluri și
racordare.</li>
<li><b>Construcții</b>: cele din care un punct iese din imobil cu mai mult de 1 cm; cele din
care o parte mai lată de 1 cm stă în afara imobilului, chiar fără niciun punct acolo; cele
care stau pe alt imobil sau nu stau pe niciun imobil; construcțiile suprapuse. O valoare din
'Imobilul construcției' care nu există în imobile este avertisment.</li>
<li><b>Poligoane invalide</b>, cu motivul și locul exact.</li>
</ul>
<h4>Ce obțineți</h4>
<p>Două layere temporare, cu problemele de tip suprafață și cu cele de tip punct, colorate după
tip, și, opțional, un raport Excel, cu toleranțele folosite în foaia Sumar.</p>
<h4>Bine de știut</h4>
<ul>
<li>O suprapunere sau un gol mai îngust decât toleranța de racordare, implicit 2 mm, provine
din rotunjirea coordonatelor și nu se raportează, oricât de lung ar fi. De-a lungul unei
limite de 100 m, o fâșie de 1 mm are 0,1 mp.</li>
<li>Un imobil care apare în mai multe fișiere CGXML, cu același ID în același UAT (sau
același IE când nu are ID) și cu limite care se suprapun pe cel puțin jumătate, se verifică o
singură dată, din fișierul cu cele mai puține subfoldere sub sursa aleasă; fișierele lui sunt
listate în rezultat. Când limitele acestor fișiere sunt în locuri diferite, fiecare fișier se
verifică ca imobil separat, iar fișierele sunt numite în rezultat.</li>
<li>Elementele fără geometrie și înregistrările fără coordonate nu se verifică; numărul lor
apare în rezultat.</li>
<li>O limită al cărei sector nu apare la niciun imobil cuprinde imobilele al căror sector nu
are limită și care stau în cea mai mare parte în ea, iar sectorul ei este numit în rezultat.
O limită fără niciun imobil nu se verifică.</li>
<li>Când sursa CGXML aleasă nu poate fi deschisă, de exemplu o arhivă ZIP deteriorată,
verificarea nu pornește. Fișierele care nu pot fi citite sunt numărate în rezultat, cu
motivul în detalii, iar celelalte se verifică.</li>
</ul>
"""),
    "building_topology": QT_TRANSLATE_NOOP("CADex", """
<p>Taie fiecare construcție care iese din imobilul pe care se află mai mult decât permite
toleranța, astfel încât să rămână în interiorul lui, și arată suprafața pierdută.</p>
<h4>Ce alegeți</h4>
<ul>
<li><b>Layerele de imobile și de construcții.</b></li>
<li><b>Imobilul construcției</b> și <b>ID imobil</b>: coloana din construcții cu ID-ul
imobilului și coloana din imobile cu aceleași valori. O construcție care stă în cea mai mare
parte pe alt imobil este marcată 'Pe alt imobil' și nu se taie. Fără aceste coloane,
construcția aparține imobilului pe care stă cea mai mare parte din ea.</li>
<li><b>Ieșirea construcțiilor:</b> cât poate ieși o construcție din imobil fără să fie tăiată,
implicit 1 cm, ca în Verificare Topologică. Construcția se taie când un punct al ei iese din
imobil cu mai mult de această valoare sau când o parte a ei mai lată decât această valoare
stă în afara imobilului, chiar fără niciun punct acolo. Cu aceeași valoare ca în Verificare
Topologică, o construcție pe care verificarea nu o raportează ca ieșită din imobil rămâne
neschimbată.</li>
<li><b>Retragerea față de limită:</b> partea care iese se taie după limita imobilului mutată
spre interior cu această distanță, implicit 2 mm.</li>
<li><b>Pragul de verificare:</b> o construcție care pierde mai mult, implicit 1 mp, este
marcată 'Pierdere peste prag'.</li>
</ul>
<h4>Ce obțineți</h4>
<p>Pentru fiecare layer de construcții, un layer temporar nou cu starea fiecărei construcții,
suprafața înainte și după tăiere și observații. Legenda arată doar stările găsite. Layerele
originale nu se modifică.</p>
<h4>Bine de știut</h4>
<ul>
<li>Fiecare parte a unei construcții din mai multe părți se taie separat. Dintr-o parte pe
care tăierea o împarte în bucăți se păstrează bucata cea mai mare, iar o parte din care nu
rămâne nimic se elimină; ambele apar în observații.</li>
<li>O construcție cu geometrie invalidă nu se taie și are starea 'Eroare', cu motivul în
observații.</li>
</ul>
"""),
    "open_settings": QT_TRANSLATE_NOOP("CADex", """
<p>În fiecare instrument, layerele și câmpurile se preselectează după numele lor. Aici
scrieți fragmentele de nume folosite în firma dumneavoastră, separate prin virgulă.
Majusculele și diacriticele nu contează, iar '*' ține locul oricărui text.</p>
<p>În fiecare instrument puteți alege oricând și manual.</p>
"""),
    "open_help": QT_TRANSLATE_NOOP("CADex", """
<p>Descrierea fiecărui instrument, ce oferă cadex.ro în browser și informații despre plugin.</p>
"""),
    "open_website": QT_TRANSLATE_NOOP("CADex", """
<p>Deschide cadex.ro, unde lucrați direct cu fișierele CGXML, în browser, fără instalare:
validatorul, comparatorul, interogările predefinite și interogările personalizate. Fișierele se
citesc în browser și rămân pe calculatorul dumneavoastră.</p>
"""),
}
