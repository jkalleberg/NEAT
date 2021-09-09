#!/usr/bin/env python
# encoding: utf-8
""" ////////////////////////////////////////////////////////////////////////////////
   ///                                                                          ///
  ///       gen_reads.py                                                       ///
 ///        VERSION 3.0: HARDER, BETTER, FASTER, STRONGER!                    ///
///////                                                                      //////
   ///      Variant and read simulator for benchmarking NGS workflows          ///
  ///                                                                         ///
 ///        Written by:     Zach Stephens                                    ///
///////     For:            DEPEND Research Group, UIUC                     ///////
   ///      Date:           May 29, 2015                                       ///
  ///       Contact:        zstephe2@illinois.edu                             ///
 ///                                                                         ///
/////////////////////////////////////////////////////////////////////////////// """

import sys
import random
import time
import bisect
import pickle
import numpy as np
import argparse
import pathlib

import pandas as pd
from Bio import SeqIO

from source.input_checking import check_file_open, is_in_range
from source.ref_func import find_n_regions
from source.bed_func import parse_bed
from source.vcf_func import parse_vcf
from source.output_file_writer import OutputFileWriter, reverse_complement, sam_flag
from source.probability import DiscreteDistribution, mean_ind_of_weighted_list
from source.SequenceContainer import SequenceContainer
from source.input_file_reader import parse_input_mutation_model
from source.ReadContainer import ReadContainer
from source.constants_and_models import ALLOWED_NUCL


def main(raw_args=None):
    """//////////////////////////////////////////////////
    ////////////    PARSE INPUT ARGUMENTS    ////////////
    //////////////////////////////////////////////////"""

    parser = argparse.ArgumentParser(description='NEAT-genReads V3.0',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter,)
    parser.add_argument('-r', type=str, required=True, metavar='reference', help="Path to reference fasta")
    parser.add_argument('-R', type=int, required=True, metavar='read length', help="The desired read length")
    parser.add_argument('-o', type=str, required=True, metavar='output_prefix',
                        help="Prefix for the output files (can be a path)")
    parser.add_argument('-c', type=float, required=False, metavar='coverage', default=10.0,
                        help="Average coverage, default is 10.0")
    parser.add_argument('-e', type=str, required=False, metavar='error_model', default=None,
                        help="Location of the file for the sequencing error model (omit to use the default)")
    parser.add_argument('-E', type=float, required=False, metavar='Error rate', default=-1,
                        help="Rescale avg sequencing error rate to this, must be between 0.0 and 0.3")
    parser.add_argument('-p', type=int, required=False, metavar='ploidy', default=2,
                        help="Desired ploidy, default = 2")
    parser.add_argument('-tr', type=str, required=False, metavar='target.bed', default=None,
                        help="Bed file containing targeted regions")
    parser.add_argument('-dr', type=str, required=False, metavar='discard_regions.bed', default=None,
                        help="Bed file with regions to discard")
    parser.add_argument('-to', type=float, required=False, metavar='off-target coverage scalar', default=0.00,
                        help="off-target coverage scalar")
    parser.add_argument('-m', type=str, required=False, metavar='model.p', default=None,
                        help="Mutation model pickle file")
    parser.add_argument('-M', type=float, required=False, metavar='avg mut rate', default=-1,
                        help="Rescale avg mutation rate to this (1/bp), must be between 0 and 0.3")
    parser.add_argument('-Mb', type=str, required=False, metavar='mut_rates.bed', default=None,
                        help="Bed file containing positional mut rates")
    parser.add_argument('-N', type=int, required=False, metavar='min qual score', default=-1,
                        help="below this quality score, replace base-calls with N's")
    parser.add_argument('-v', type=str, required=False, metavar='vcf.file', default=None,
                        help="Input VCF file of variants to include")
    parser.add_argument('--pe', nargs=2, type=int, required=False, metavar=('<int>', '<int>'), default=(None, None),
                        help='Paired-end fragment length mean and std')
    parser.add_argument('--pe-model', type=str, required=False, metavar='<str>', default=None,
                        help='empirical fragment length distribution')
    parser.add_argument('--gc-model', type=str, required=False, metavar='<str>', default=None,
                        help='empirical GC coverage bias distribution')
    parser.add_argument('--bam', required=False, action='store_true', default=False, help='output golden BAM file ('
                                                                                          'Note that this will '
                                                                                          'drastically slow NEAT down)')
    parser.add_argument('--vcf', required=False, action='store_true', default=False, help='output golden VCF file')
    parser.add_argument('--fa', required=False, action='store_true', default=False,
                        help='output FASTA instead of FASTQ')
    parser.add_argument('--rng', type=int, required=False, metavar='<int>', default=-1,
                        help='rng seed value; identical RNG value should produce identical runs of the program, so '
                             'things like read locations, variant positions, error positions, etc, '
                             'should all be the same.')
    parser.add_argument('--no-fastq', required=False, action='store_true', default=False,
                        help='bypass fastq generation')
    parser.add_argument('--discard-offtarget', required=False, action='store_true', default=False,
                        help='discard reads outside of targeted regions')
    parser.add_argument('--force-coverage', required=False, action='store_true', default=False,
                        help='[debug] ignore fancy models, force coverage to be constant')
    parser.add_argument('--rescale-qual', required=False, action='store_true', default=False,
                        help='Rescale quality scores to match -E input')
    # TODO implement a broader debugging scheme for subclasses.
    parser.add_argument('-d', required=False, action='store_true', default=False, help='Activate Debug Mode')
    args = parser.parse_args(raw_args)

    """
    Some constants needed for analysis
    """

    # target window size for read sampling. How many times bigger than read/frag length
    window_target_scale = 100

    """
    Set variables for processing
    """

    # absolute path to this script
    sim_path = pathlib.Path(__file__).resolve().parent

    # if coverage val for a given window/position is below this value, consider it effectively zero.
    low_cov_thresh = 50

    # required args
    (reference, read_len, out_prefix) = (args.r, args.R, args.o)
    # various dataset parameters
    (coverage, ploids, target_bed, discard_bed, se_model, se_rate, mut_model, mut_rate, mut_bed, input_vcf) = \
        (args.c, args.p, args.tr, args.dr, args.e, args.E, args.m, args.M, args.Mb, args.v)
    # cancer params (disabled currently)
    # (cancer, cancer_model, cancer_purity) = (args.cancer, args.cm, args.cp)
    # For now these are set to false
    (cancer, cancer_model, cancer_purity) = (False, None, 0.8)

    (off_target_scalar, off_target_discard, force_coverage, rescale_qual) = (args.to,
                                                                             args.discard_offtarget,
                                                                             args.force_coverage, args.rescale_qual)
    # important flags
    (save_bam, save_vcf, create_fasta, no_fastq) = \
        (args.bam, args.vcf, args.fa, args.no_fastq)

    # sequencing model parameters
    (fragment_size, fragment_std) = args.pe
    (fraglen_model, gc_bias_model) = args.pe_model, args.gc_model
    n_max_qual = args.N

    rng_seed = args.rng

    debug = args.d

    """
    INPUT ERROR CHECKING
    """

    # Check that files are real, if provided
    check_file_open(reference, 'ERROR: could not open reference, {}'.format(reference), required=True)
    check_file_open(input_vcf, 'ERROR: could not open input VCF, {}'.format(input_vcf), required=False)
    check_file_open(target_bed, 'ERROR: could not open input BED, {}'.format(target_bed), required=False)

    # if user specified no fastq, not fasta only, and no bam and no vcf, then print error and exit.
    if no_fastq and not create_fasta and not save_bam and not save_vcf:
        print('\nERROR: No files would be written.\n')
        sys.exit(1)

    if create_fasta:
        no_fastq = True
        print("Writing output in FASTA format...")

    if no_fastq:
        print('Bypassing FASTQ generation...')

    only_vcf = no_fastq and save_vcf and not save_bam and not create_fasta
    if only_vcf:
        print('Only producing VCF output...')

    if (fragment_size is None and fragment_std is not None) or (fragment_size is not None and fragment_std is None):
        print('\nERROR: --pe argument takes 2 space-separated arguments.\n')
        sys.exit(1)

    # If user specified mean/std, or specified an empirical model, then the reads will be paired_ended
    # If not, then we're doing single-end reads.
    if (fragment_size is not None and fragment_std is not None) or (fraglen_model is not None) and not create_fasta:
        paired_end = True
    else:
        paired_end = False

    if rng_seed == -1:
        rng_seed = random.randint(1, 99999999)
    random.seed(rng_seed)

    is_in_range(read_len, 10, 1000000, 'Error: -R must be between 10 and 1,000,000')
    is_in_range(coverage, 0, 1000000, 'Error: -c must be between 0 and 1,000,000')
    is_in_range(ploids, 1, 100, 'Error: -p must be between 1 and 100')
    is_in_range(off_target_scalar, 0, 1, 'Error: -to must be between 0 and 1')

    if se_rate != -1:
        is_in_range(se_rate, 0, 0.3, 'Error: -E must be between 0 and 0.3')
    else:
        se_rate = None

    if n_max_qual != -1:
        is_in_range(n_max_qual, 1, 40, 'Error: -N must be between 1 and 40')

    """
    LOAD INPUT MODELS
    """

    # mutation models
    mut_model = parse_input_mutation_model(mut_model, 1)
    if cancer:
        cancer_model = parse_input_mutation_model(cancer_model, 2)
    if mut_rate < 0.:
        mut_rate = None

    if mut_rate != -1 and mut_rate is not None:
        is_in_range(mut_rate, 0.0, 0.3, 'Error: -M must be between 0 and 0.3')

    # sequencing error model
    if se_model is None:
        print('Using default sequencing error model.')
        se_model = sim_path / 'models/errorModel_toy.p'

    # probably need to do some sanity checking

    # Create read container.
    se_class = ReadContainer(read_len, se_model, se_rate, rescale_qual)

    # GC-bias model
    if gc_bias_model is None:
        print('Using default gc-bias model.')
        gc_bias_model = sim_path / 'models/gcBias_toy.p'
        try:
            [gc_scale_count, gc_scale_val] = pickle.load(open(gc_bias_model, 'rb'))
        except IOError:
            print("\nProblem reading the default gc-bias model.\n")
            sys.exit(1)
        gc_window_size = gc_scale_count[-1]
    else:
        try:
            [gc_scale_count, gc_scale_val] = pickle.load(open(gc_bias_model, 'rb'))
        except IOError:
            print("\nProblem reading the gc-bias model.\n")
            sys.exit(1)
        gc_window_size = gc_scale_count[-1]

    # Assign appropriate values to the needed variables if we're dealing with paired-ended data
    if paired_end:
        # Empirical fragment length distribution, if input model is specified
        if fraglen_model is not None:
            print('Using empirical fragment length distribution.')
            try:
                [potential_values, potential_prob] = pickle.load(open(fraglen_model, 'rb'))
            except IOError:
                print(sys.exc_info()[2])
                print('\nProblem loading the empirical fragment length model.\n')
                sys.exit(1)

            fraglen_values = []
            fraglen_probability = []
            for i in range(len(potential_values)):
                if potential_values[i] > read_len:
                    fraglen_values.append(potential_values[i])
                    fraglen_probability.append(potential_prob[i])

            # TODO add some validation and sanity-checking code here...
            fraglen_distribution = DiscreteDistribution(fraglen_probability, fraglen_values)
            fragment_size = fraglen_values[mean_ind_of_weighted_list(fraglen_probability)]

        # Using artificial fragment length distribution, if the parameters were specified
        # fragment length distribution: normal distribution that goes out to +- 6 standard deviations
        elif fragment_size is not None and fragment_std is not None:
            print('Using artificial fragment length distribution. mean=' + str(fragment_size) + ', std=' + str(
                    fragment_std))
            if fragment_std == 0:
                fraglen_distribution = DiscreteDistribution([1], [fragment_size], degenerate_val=fragment_size)
            else:
                potential_values = range(fragment_size - 6 * fragment_std, fragment_size + 6 * fragment_std + 1)
                fraglen_values = []
                for i in range(len(potential_values)):
                    if potential_values[i] > read_len:
                        fraglen_values.append(potential_values[i])
                fraglen_probability = [np.exp(-(((n - float(fragment_size)) ** 2) / (2 * (fragment_std ** 2)))) for n in
                                       fraglen_values]
                fraglen_distribution = DiscreteDistribution(fraglen_probability, fraglen_values)

    """
    Process Inputs
    """

    # TODO check if this index can work, maybe it's faster
    tt = time.time()
    print(f'reading {reference}... ')

    ref_index = SeqIO.index(reference, 'fasta')
    reference_chromosomes = list(ref_index.keys())
    begins_with_chr = False
    if all(k.startswith('chr') for k in reference_chromosomes):
        begins_with_chr = True

    print('{0:.3f} (sec)'.format(time.time() - tt))

    if paired_end:
        n_handling = ('random', fragment_size)
    else:
        n_handling = ('ignore', read_len)

    # parse input variants, if present
    # TODO read this in as a pandas dataframe
    input_variants = None
    printed_warning = False
    if input_vcf:
        if cancer:
            (sample_names, input_variants) = parse_vcf(input_vcf, tumor_normal=True, ploidy=ploids, debug=debug)
            # TODO figure out what these were going to be used for
            tumor_ind = sample_names.index('tumor_sample_split')
            normal_ind = sample_names.index('normal_sample_split')
        else:
            (sample_names, input_variants) = parse_vcf(input_vcf, ploidy=ploids, debug=debug)

        # Remove any chromosomes that aren't in the reference.
        input_variants_chroms = list(set(list(input_variants.CHROM)))
        for item in input_variants_chroms:
            if item not in reference_chromosomes and not printed_warning:
                print(f'Warning: ignoring all input vcf records for {item} because it is not found in the reference.')
                print(f'\tIf this is unexpected, check that that {item} matches reference name exactly.')
                printed_warning = True
                input_variants = input_variants[input_variants['CHROM'] != item]

        for chrom in reference_chromosomes:
            n_skipped = [0, 0, 0]
            if chrom in input_variants_chroms:
                for index, row in input_variants[input_variants['CHROM'] == chrom].iterrows():
                    span = (row['POS'], row['POS'] + len(row['REF']))
                    # -1 because going from VCF coords to array coords
                    r_seq = str(ref_index[chrom].seq[span[0] - 1:span[1] - 1])
                    # Checks if there are any invalid nucleotides in the vcf items
                    any_bad_nucl = any((nn not in ALLOWED_NUCL) for nn in
                                       [item for sublist in row['alt_split'] for item in sublist])
                    # Ensure reference sequence matches the nucleotide in the vcf
                    if r_seq != row['REF']:
                        n_skipped[0] += 1
                        input_variants.drop(index, inplace=True)
                        continue
                    # Ensure that we aren't trying to insert into an N region
                    elif 'N' in r_seq:
                        n_skipped[1] += 1
                        input_variants.drop(index, inplace=True)
                        continue
                    # Ensure that we don't insert any disallowed characters
                    elif any_bad_nucl:
                        n_skipped[2] += 1
                        input_variants.drop(index, inplace=True)
                        continue

                print('found', len(input_variants), 'valid variants for ' +
                      chrom + ' in input VCF...')
                if any(n_skipped):
                    print(sum(n_skipped), 'variants skipped...')
                    print(' - [' + str(n_skipped[0]) + '] ref allele does not match reference')
                    print(' - [' + str(n_skipped[1]) + '] attempting to insert into N-region')
                    print(' - [' + str(n_skipped[2]) + '] alt allele contains non-ACGT characters')

    # parse input targeted regions, if present
    target_regions = parse_bed(target_bed, reference_chromosomes, begins_with_chr, False, debug)

    # parse discard bed similarly
    discard_regions = parse_bed(discard_bed, reference_chromosomes, begins_with_chr, False, debug)

    # parse input mutation rate rescaling regions, if present
    mutation_rate_regions, mutation_rate_values = parse_bed(mut_bed, reference_chromosomes,
                                                            begins_with_chr, True, debug)

    # initialize output files (part I)
    bam_header = None
    if save_bam:
        # TODO wondering if this is actually needed in the bam_header
        # The info is needed, but may exist in the index biopython creates.
        bam_header = ref_index
    vcf_header = None
    if save_vcf:
        vcf_header = [reference]

    # initialize output files (part II)
    # TODO figure out how to do this more efficiently. Write the files at the end.
    #  At least move this down so the file isn't created and opened hours before there's anything to put in it.
    # The cancer part currently does nothing. The code is unreachable as written
    if cancer:
        output_file_writer = OutputFileWriter(out_prefix + '_normal', paired=paired_end, bam_header=bam_header,
                                              vcf_header=vcf_header,
                                              no_fastq=no_fastq, write_fasta=create_fasta,
                                              save_bam=save_bam, save_vcf=save_vcf)
        output_file_writer_cancer = OutputFileWriter(out_prefix + '_tumor', paired=paired_end, bam_header=bam_header,
                                                     vcf_header=vcf_header,
                                                     no_fastq=no_fastq, write_fasta=create_fasta,
                                                     save_bam=save_bam, save_vcf=save_vcf)
    else:
        output_file_writer = OutputFileWriter(out_prefix, paired=paired_end, bam_header=bam_header,
                                              vcf_header=vcf_header,
                                              no_fastq=no_fastq,
                                              write_fasta=create_fasta,
                                              save_bam=save_bam, save_vcf=save_vcf)
    # Using pathlib to make this more machine agnostic
    out_prefix_name = pathlib.Path(out_prefix).name

    """
    LET'S GET THIS PARTY STARTED...
    """
    # keep track of the number of reads we've sampled, for read-names
    # TODO this sounds vaguely unnecessary
    read_name_count = 1
    unmapped_records = []

    for chrom in ref_index.keys():

        # read in reference sequence and notate blocks of Ns
        ref_sequence = ref_index[chrom].seq

        n_regions = find_n_regions(ref_sequence, n_handling)

        # count total bp we'll be spanning so we can get an idea of how far along we are
        # (for printing progress indicators)
        total_bp_span = sum([n[1] - n[0] for n in n_regions['non_N']])
        current_progress = 0
        current_percent = 0
        have_printed100 = False

        """Prune invalid input variants, e.g variants that:
                - try to delete or alter any N characters
                - don't match the reference base at their specified position
                - any alt allele contains anything other than allowed characters"""
        valid_variants_from_vcf = []
        n_skipped = [0, 0, 0]

        # TODO add large random structural variants

        # determine sampling windows based on read length, large N regions, and structural mutations.
        # in order to obtain uniform coverage, windows should overlap by:
        # - read_len, if single-end reads
        # - fragment_size (mean), if paired-end reads
        # ploidy is fixed per large sampling window,
        # coverage distributions due to GC% and targeted regions are specified within these windows
        all_variants_out = {}
        sequences = None
        if paired_end:
            target_size = window_target_scale * fragment_size
            overlap = fragment_size
            overlap_min_window_size = max(fraglen_distribution.values) + 10
        else:
            target_size = window_target_scale * read_len
            overlap = read_len
            overlap_min_window_size = read_len + 10

        print('--------------------------------')
        if only_vcf:
            print('generating vcf...')
        elif create_fasta:
            print('generating mutated fasta...')
        else:
            print('sampling reads...')
        tt = time.time()
        # start the progress bar
        print("[", end='', flush=True)

        # Applying variants to non-N regions
        for i in range(len(n_regions['non_N'])):
            (initial_position, final_position) = n_regions['non_N'][i]
            number_target_windows = max([1, (final_position - initial_position) // target_size])
            base_pair_distance = int((final_position - initial_position) / float(number_target_windows))

            # if for some reason our region is too small to process, skip it! (sorry)
            if number_target_windows == 1 and (final_position - initial_position) < overlap_min_window_size:
                continue

            start = initial_position
            end = min([start + base_pair_distance, final_position])
            vars_from_prev_overlap = []
            vars_cancer_from_prev_overlap = []
            v_index_from_prev = 0
            is_last_time = False

            while True:
                # which inserted variants are in this window?
                vars_in_window = []
                updated = False
                for j in range(v_index_from_prev, len(valid_variants_from_vcf)):
                    variants_position = valid_variants_from_vcf[j][0]
                    # update: changed <= to <, so variant cannot be inserted in first position
                    if start < variants_position < end:
                        # vcf --> array coords
                        vars_in_window.append(tuple([variants_position - 1] + list(valid_variants_from_vcf[j][1:])))
                    if variants_position >= end - overlap - 1 and updated is False:
                        updated = True
                        v_index_from_prev = j
                    if variants_position >= end:
                        break

                # determine which structural variants will affect our sampling window positions
                structural_vars = []
                for row in vars_in_window:
                    # change: added abs() so that insertions are also buffered.
                    buffer_needed = max([max([abs(len(row[1]) - len(alt_allele)), 1]) for alt_allele in row[2]])
                    # -1 because going from VCF coords to array coords
                    structural_vars.append((row[0] - 1, buffer_needed))

                # adjust end-position of window based on inserted structural mutations
                keep_going = True
                buffer_added = 0
                while keep_going:
                    keep_going = False
                    for row in structural_vars:
                        # adding "overlap" here to prevent SVs from being introduced in overlap regions
                        # (which can cause problems if random mutations from the previous window land on top of them)
                        delta = (end - 1) - (row[0] + row[1]) - 2 - overlap
                        if delta < 0:
                            buffer_added = -delta
                            end += buffer_added
                            keep_going = True
                            break
                next_start = end - overlap
                next_end = min([next_start + base_pair_distance, final_position])
                if next_end - next_start < base_pair_distance:
                    end = next_end
                    is_last_time = True

                # print progress indicator
                if debug:
                    print(f'PROCESSING WINDOW: {(start, end), [buffer_added]}, '
                          f'next: {(next_start, next_end)}, isLastTime: {is_last_time}')
                current_progress += end - start
                new_percent = int((current_progress * 100) / float(total_bp_span))
                if new_percent > current_percent:
                    if new_percent <= 99 or (new_percent == 100 and not have_printed100):
                        if new_percent % 10 == 1 or new_percent == 100:
                            print('-', end='', flush=True)
                    current_percent = new_percent
                    if current_percent == 100:
                        have_printed100 = True

                skip_this_window = False

                # compute coverage modifiers
                coverage_avg = None
                coverage_dat = [gc_window_size, gc_scale_val, []]
                target_hits = 0
                if not target_regions:
                    coverage_dat[2] = [1.0] * (end - start)
                else:
                    if chrom not in target_regions:
                        coverage_dat[2] = [off_target_scalar] * (end - start)
                    else:
                        for j in range(start, end):
                            if not (bisect.bisect(target_regions[chrom], j) % 2):
                                coverage_dat[2].append(1.0)
                                target_hits += 1
                            else:
                                coverage_dat[2].append(off_target_scalar)

                # off-target and we're not interested?
                if off_target_discard and target_hits <= read_len:
                    coverage_avg = 0.0
                    skip_this_window = True

                if debug:
                    print(len(coverage_dat[2]), sum(coverage_dat[2]))
                if sum(coverage_dat[2]) < low_cov_thresh:
                    coverage_avg = 0.0
                    skip_this_window = True

                # check for small window sizes
                if (end - start) < overlap_min_window_size:
                    skip_this_window = True

                if skip_this_window:
                    # skip window, save cpu time
                    start = next_start
                    end = next_end
                    if is_last_time:
                        break
                    if end >= final_position:
                        is_last_time = True
                    vars_from_prev_overlap = []
                    continue

                # construct sequence data that we will sample reads from
                if not sequences:
                    sequences = SequenceContainer(start, ref_sequence[start:end], ploids, overlap, read_len,
                                                  [mut_model] * ploids, mut_rate, only_vcf)
                    if [cigar for cigar in sequences.all_cigar[0] if len(cigar) != 100] or \
                            [cig for cig in sequences.all_cigar[1] if len(cig) != 100]:
                        print("There's a cigar that's off.")
                        # pdb.set_trace()
                        sys.exit(1)
                else:
                    sequences.update(start, ref_sequence[start:end], ploids, overlap, read_len, [mut_model] * ploids,
                                     mut_rate)
                    if [cigar for cigar in sequences.all_cigar[0] if len(cigar) != 100] or \
                            [cig for cig in sequences.all_cigar[1] if len(cig) != 100]:
                        print("There's a cigar that's off.")
                        # pdb.set_trace()
                        sys.exit(1)

                # insert variants
                sequences.insert_mutations(vars_from_prev_overlap + vars_in_window)
                all_inserted_variants = sequences.random_mutations()
                # print all_inserted_variants

                # init coverage
                if sum(coverage_dat[2]) >= low_cov_thresh:
                    if paired_end:
                        coverage_avg = sequences.init_coverage(tuple(coverage_dat), frag_dist=fraglen_distribution)
                    else:
                        coverage_avg = sequences.init_coverage(tuple(coverage_dat))

                # unused cancer stuff
                if cancer:
                    tumor_sequences = SequenceContainer(start, ref_sequence[start:end], ploids, overlap, read_len,
                                                        [cancer_model] * ploids, mut_rate, coverage_dat)
                    tumor_sequences.insert_mutations(vars_cancer_from_prev_overlap + all_inserted_variants)
                    all_cancer_variants = tumor_sequences.random_mutations()

                # which variants do we need to keep for next time (because of window overlap)?
                vars_from_prev_overlap = []
                vars_cancer_from_prev_overlap = []
                for row in all_inserted_variants:
                    if row[0] >= end - overlap - 1:
                        vars_from_prev_overlap.append(row)
                if cancer:
                    for row in all_cancer_variants:
                        if row[0] >= end - overlap - 1:
                            vars_cancer_from_prev_overlap.append(row)

                # if we're only producing VCF, no need to go through the hassle of generating reads
                if only_vcf:
                    pass
                else:
                    window_span = end - start

                    if paired_end:
                        if force_coverage:
                            reads_to_sample = int((window_span * float(coverage)) / (2 * read_len)) + 1
                        else:
                            reads_to_sample = int((window_span * float(coverage) * coverage_avg) / (2 * read_len)) + 1
                    else:
                        if force_coverage:
                            reads_to_sample = int((window_span * float(coverage)) / read_len) + 1
                        else:
                            reads_to_sample = int((window_span * float(coverage) * coverage_avg) / read_len) + 1

                    # if coverage is so low such that no reads are to be sampled, skip region
                    #      (i.e., remove buffer of +1 reads we add to every window)
                    if reads_to_sample == 1 and sum(coverage_dat[2]) < low_cov_thresh:
                        reads_to_sample = 0

                    # sample reads
                    for k in range(reads_to_sample):

                        is_unmapped = []
                        if paired_end:
                            my_fraglen = fraglen_distribution.sample()
                            my_read_data = sequences.sample_read(se_class, my_fraglen)
                            # skip if we failed to find a valid position to sample read
                            if my_read_data is None:
                                continue
                            if my_read_data[0][0] is None:
                                is_unmapped.append(True)
                            else:
                                is_unmapped.append(False)
                                # adjust mapping position based on window start
                                my_read_data[0][0] += start
                            if my_read_data[1][0] is None:
                                is_unmapped.append(True)
                            else:
                                is_unmapped.append(False)
                                my_read_data[1][0] += start
                        else:
                            my_read_data = sequences.sample_read(se_class)
                            # skip if we failed to find a valid position to sample read
                            if my_read_data is None:
                                continue
                            # unmapped read (lives in large insertion)
                            if my_read_data[0][0] is None:
                                is_unmapped = [True]
                            else:
                                is_unmapped = [False]
                                # adjust mapping position based on window start
                                my_read_data[0][0] += start

                        # are we discarding offtargets?
                        outside_boundaries = []
                        if off_target_discard and target_bed is not None:
                            outside_boundaries += [bisect.bisect(target_regions[chrom], n[0]) % 2 for n
                                                   in my_read_data]
                            outside_boundaries += [
                                bisect.bisect(target_regions[chrom], n[0] + len(n[2])) % 2 for n in
                                my_read_data]
                        if discard_bed is not None:
                            outside_boundaries += [bisect.bisect(discard_regions[chrom], n[0]) % 2 for
                                                   n in my_read_data]
                            outside_boundaries += [
                                bisect.bisect(discard_regions[chrom], n[0] + len(n[2])) % 2 for n in
                                my_read_data]

                        if len(outside_boundaries) and any(outside_boundaries):
                            continue

                        my_read_name = out_prefix_name + '-' + chrom + '-' + str(read_name_count)
                        read_name_count += len(my_read_data)

                        # if desired, replace all low-quality bases with Ns
                        if n_max_qual > -1:
                            for j in range(len(my_read_data)):
                                my_read_string = [n for n in my_read_data[j][2]]
                                for m in range(len(my_read_data[j][3])):
                                    adjusted_qual = ord(my_read_data[j][3][m]) - se_class.off_q
                                    if adjusted_qual <= n_max_qual:
                                        my_read_string[m] = 'N'
                                my_read_data[j][2] = ''.join(my_read_string)

                        # flip a coin, are we forward or reverse strand?
                        is_forward = (random.random() < 0.5)

                        # if read (or read + mate for PE) are unmapped, put them at end of bam file
                        if all(is_unmapped):
                            if paired_end:
                                if is_forward:
                                    flag1 = sam_flag(['paired', 'unmapped', 'mate_unmapped', 'first', 'mate_reverse'])
                                    flag2 = sam_flag(['paired', 'unmapped', 'mate_unmapped', 'second', 'reverse'])
                                else:
                                    flag1 = sam_flag(['paired', 'unmapped', 'mate_unmapped', 'second', 'mate_reverse'])
                                    flag2 = sam_flag(['paired', 'unmapped', 'mate_unmapped', 'first', 'reverse'])
                                unmapped_records.append((my_read_name + '/1', my_read_data[0], flag1))
                                unmapped_records.append((my_read_name + '/2', my_read_data[1], flag2))
                            else:
                                flag1 = sam_flag(['unmapped'])
                                unmapped_records.append((my_read_name + '/1', my_read_data[0], flag1))

                        # write SE output
                        if len(my_read_data) == 1:
                            if not no_fastq:
                                if is_forward:
                                    output_file_writer.write_fastq_record(my_read_name, my_read_data[0][2],
                                                                          my_read_data[0][3])
                                else:
                                    output_file_writer.write_fastq_record(my_read_name,
                                                                          reverse_complement(my_read_data[0][2]),
                                                                          my_read_data[0][3][::-1])
                            if save_bam:
                                if is_unmapped[0] is False:
                                    if is_forward:
                                        flag1 = 0
                                        output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                            my_read_name,
                                                                            my_read_data[0][0],
                                                                            my_read_data[0][1], my_read_data[0][2],
                                                                            my_read_data[0][3],
                                                                            output_sam_flag=flag1)
                                    else:
                                        flag1 = sam_flag(['reverse'])
                                        output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                            my_read_name,
                                                                            my_read_data[0][0],
                                                                            my_read_data[0][1], my_read_data[0][2],
                                                                            my_read_data[0][3],
                                                                            output_sam_flag=flag1)
                        # write PE output
                        elif len(my_read_data) == 2:
                            if not no_fastq:
                                output_file_writer.write_fastq_record(my_read_name, my_read_data[0][2],
                                                                      my_read_data[0][3],
                                                                      read2=my_read_data[1][2],
                                                                      quality2=my_read_data[1][3],
                                                                      orientation=is_forward)
                            if save_bam:
                                if is_unmapped[0] is False and is_unmapped[1] is False:
                                    if is_forward:
                                        flag1 = sam_flag(['paired', 'proper', 'first', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'proper', 'second', 'reverse'])
                                    else:
                                        flag1 = sam_flag(['paired', 'proper', 'second', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'proper', 'first', 'reverse'])
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[0][0],
                                                                        my_read_data[0][1], my_read_data[0][2],
                                                                        my_read_data[0][3],
                                                                        output_sam_flag=flag1,
                                                                        mate_pos=my_read_data[1][0])
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[1][0],
                                                                        my_read_data[1][1], my_read_data[1][2],
                                                                        my_read_data[1][3],
                                                                        output_sam_flag=flag2,
                                                                        mate_pos=my_read_data[0][0])
                                elif is_unmapped[0] is False and is_unmapped[1] is True:
                                    if is_forward:
                                        flag1 = sam_flag(['paired', 'first', 'mate_unmapped', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'second', 'unmapped', 'reverse'])
                                    else:
                                        flag1 = sam_flag(['paired', 'second', 'mate_unmapped', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'first', 'unmapped', 'reverse'])
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[0][0],
                                                                        my_read_data[0][1], my_read_data[0][2],
                                                                        my_read_data[0][3],
                                                                        output_sam_flag=flag1,
                                                                        mate_pos=my_read_data[0][0])
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[0][0],
                                                                        my_read_data[1][1], my_read_data[1][2],
                                                                        my_read_data[1][3],
                                                                        output_sam_flag=flag2,
                                                                        mate_pos=my_read_data[0][0],
                                                                        aln_map_quality=0)
                                elif is_unmapped[0] is True and is_unmapped[1] is False:
                                    if is_forward:
                                        flag1 = sam_flag(['paired', 'first', 'unmapped', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'second', 'mate_unmapped', 'reverse'])
                                    else:
                                        flag1 = sam_flag(['paired', 'second', 'unmapped', 'mate_reverse'])
                                        flag2 = sam_flag(['paired', 'first', 'mate_unmapped', 'reverse'])
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[1][0],
                                                                        my_read_data[0][1], my_read_data[0][2],
                                                                        my_read_data[0][3],
                                                                        output_sam_flag=flag1,
                                                                        mate_pos=my_read_data[1][0],
                                                                        aln_map_quality=0)
                                    output_file_writer.write_bam_record(list(ref_index.keys()).index(chrom),
                                                                        my_read_name, my_read_data[1][0],
                                                                        my_read_data[1][1], my_read_data[1][2],
                                                                        my_read_data[1][3],
                                                                        output_sam_flag=flag2,
                                                                        mate_pos=my_read_data[1][0])
                        else:
                            print('\nError: Unexpected number of reads generated...\n')
                            sys.exit(1)

                    if not is_last_time:
                        output_file_writer.flush_buffers(bam_max=next_start)
                    else:
                        output_file_writer.flush_buffers(bam_max=end + 1)

                # tally up all the variants that got successfully introduced
                for row in all_inserted_variants:
                    all_variants_out[row] = True

                # prepare indices of next window
                start = next_start
                end = next_end
                if is_last_time:
                    break
                if end >= final_position:
                    is_last_time = True

        print(']', flush=True)

        if only_vcf:
            print('VCF generation completed in ', end='')
        else:
            print('Read sampling completed in ', end='')
        print(int(time.time() - tt), '(sec)')

        # write all output variants for this reference
        if save_vcf:
            print('Writing output VCF...')
            for k in sorted(all_variants_out.keys()):
                current_ref = chrom
                my_id = '.'
                my_quality = '.'
                my_filter = 'PASS'
                # k[0] + 1 because we're going back to 1-based vcf coords
                output_file_writer.write_vcf_record(current_ref, str(int(k[0]) + 1), my_id, k[1], k[2], my_quality,
                                                    my_filter, k[4])

    # write unmapped reads to bam file
    if save_bam and len(unmapped_records):
        print('writing unmapped reads to bam file...')
        for umr in unmapped_records:
            if paired_end:
                output_file_writer.write_bam_record(-1, umr[0], 0, umr[1][1], umr[1][2], umr[1][3],
                                                    output_sam_flag=umr[2],
                                                    mate_pos=0,
                                                    aln_map_quality=0)
            else:
                output_file_writer.write_bam_record(-1, umr[0], 0, umr[1][1], umr[1][2], umr[1][3],
                                                    output_sam_flag=umr[2],
                                                    aln_map_quality=0)

    # close output files
    output_file_writer.close_files()
    if cancer:
        output_file_writer_cancer.close_files()
    ref_index.close()


if __name__ == '__main__':
    main()
