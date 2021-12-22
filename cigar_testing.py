#!/usr/bin/env python

import mappy as mp
a = mp.Aligner("/Users/keshavgandhi/Downloads/H1N1_HA.fa")  # load or build index
for name, seq, qual in mp.fastx_read("/Users/keshavgandhi/Downloads/H1N1_one_record.fq"): # read a fasta/q sequence
        for hit in a.map(seq): # traverse alignments
                print("{}\t{}\t{}\t{}".format(hit.ctg, hit.r_st, hit.r_en, hit.cigar_str))
