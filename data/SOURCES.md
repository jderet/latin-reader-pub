# Sources des données linguistiques embarquées

## `lemma_reference.tsv.gz` — liste des lemmes attestés

**LiLa Lemma Bank**, CIRCSE, Università Cattolica del Sacro Cuore, Milan.
Licence : **CC BY-SA 4.0** — usage commercial autorisé, partage à l'identique.
<https://github.com/CIRCSE/LiLa_Lemma-Bank> · DOI 10.5281/zenodo.4017229

Passarotti, Cecchini, Franzini, Iurescia, Litta, Mambrini, Moretti, Pedonese,
Pellegrini, Ruffolo, Sprugnoli, Testori. Projet financé par le Conseil
européen de la recherche (Horizon 2020, convention n° 769994).

Sert à vérifier qu'un lemme **existe**.

## `form_lemma.tsv.gz` — couples forme fléchie / lemme

Treebanks **Universal Dependencies** pour le latin :

| Corpus | Licence |
|---|---|
| UD_Latin-ITTB | CC BY-NC-SA 3.0 |
| UD_Latin-PROIEL | CC BY-NC-SA 3.0 |
| UD_Latin-Perseus | CC BY-NC-SA 2.5 |
| UD_Latin-UDante | CC BY-NC-SA 3.0 |
| UD_Latin-LLCT | CC BY-SA 4.0 |

Licence résultante : **CC BY-NC-SA — usage non commercial uniquement.**

Sert à vérifier qu'une forme donnée **se rattache** effectivement à ce lemme.

### Conséquence pratique

Tant que ce fichier est présent, l'application ne peut pas être exploitée
commercialement. Pour lever la restriction au prix d'une couverture bien
moindre (environ 50 % au lieu de 90 %) :

    python tools/build_form_lemma.py --free

Cette variante n'utilise que LLCT, en CC BY-SA 4.0.

## `short_gloss.tsv.gz` — lexique court pour les info-bulles

**Collatinus**, Yves Ouvrard et Philippe Verkerk, hébergé par Biblissima.
Licence : **GNU GPL**.
<https://github.com/biblissima/collatinus>

Fichiers `bin/data/lemmes.fr` et `lem_ext.fr`, dont les traductions ont été
réduites à un ou deux sens (16 caractères en médiane) pour tenir sous un mot
du texte. 75 441 entrées.

### Conséquence pratique

La GPL est une licence à réciprocité : redistribuer ce fichier impose de
placer l'ensemble du projet sous GPL. Si cela ne vous convient pas,
supprimez `data/short_gloss.tsv.gz` — l'application fonctionne sans, et
l'info-bulle n'affiche alors que le lemme.

    python tools/build_short_lexicon.py   # pour le reconstruire

## `gaffiot.tsv.gz` — base lexicale du latin classique

**Gaffiot 2016**, numérisation de Gérard Gréco et collaborateurs.
Licence : **CC BY-NC-SA** — usage non commercial.

68 200 lemmes, avec leur vedette complète (`flūmĕn, ĭnis`), leur catégorie,
leur genre et une glose française. Sert à trois choses : afficher la vedette
plutôt que le lemme nu, départager deux candidats (figurer au Gaffiot est un
indice de fréquence, cette liste étant bien plus resserrée que le Lemma Bank),
et compléter les gloses de Collatinus.

    python tools/build_gaffiot.py chemin/gaffiot.csv

## Fichiers retirés

`lexicon.tsv` et `dictionary.tsv` étaient de petits fichiers écrits à la main
pour le seul passage de César qui servait d'exemple. Ils ont été supprimés :
la table des formes attestées, la liste de lemmes du LiLa Lemma Bank, le
Gaffiot et le lexique Collatinus les couvrent entièrement. La qualité de la
lemmatisation est inchangée — vérifiée sur César, Cicéron et Virgile.
