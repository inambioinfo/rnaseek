from collections import defaultdict
import sys
import warnings

import numpy as np
import gffutils
import pandas as pd
import pybedtools
from pyfaidx import Fasta

from Bio import SeqIO
from Bio.SeqRecord import SeqRecord

class SpliceAnnotator(object):

    def __init__(self, miso_ids, splice_type, genome, genome_fasta=None):
        """

        Parameters
        ----------
        miso_ids : list-like
            List of strings of miso ids, e.g.
            "chr1:100:200:+@chr1:300:400:+@chr1:500:600:" is an example of
            a skipped exon MISO ID, where the middle exon is alternative.
        splice_type : str
            The type of splicing event. Currently only 'SE' (skipped exon) and
             'MXE' (mutually exclusive exon) are supported
        genome : str
            Name of the genome, e.g. "hg19" or "mm10"
        genome_fasta : str
            Location of the (indexed!) genome fasta file. If it's not indexed,
            grabbing the sequences of features won't work. You'll need to use
            "faidx" to index the genome fasta file
        """
        self.miso_ids = miso_ids
        self.splice_type = splice_type
        self.genome_fasta = genome_fasta

        # Only use miso IDs that are in the genome fasta, so the order of the
        # exon_bedtools and exon_fastas are exactly the same.
        if self.genome_fasta is not None:
            fa = Fasta(self.genome_fasta)
            chromosomes = fa.keys()
            n_bad_chrom = sum(1 for x in self.miso_ids
                                            if x.split(':')[0] not in chromosomes)
            sys.stderr.write("Removing {} miso ids whose chromosomes do not match"
                             " with the given genome fasta"
                             " file".format(n_bad_chrom))
            self.miso_ids = [x for x in self.miso_ids
                             if x.split(':')[0] in chromosomes]

        self.n_exons = None
        if splice_type == 'SE':
            self.n_exons = 3
        elif splice_type == 'MXE':
            self.n_exons = 4

        self.exon_ids = map(self.miso_id_to_exon_ids, self.miso_ids)
        self.exon_coords = map(lambda x: map(self.miso_exon_to_coords,
                                             x.split('@')),
                               self.miso_ids)

        # Make a bedtool for each exon
        self.exon_bedtools = map(self.coords_to_bedtool,
                                 zip(*self.exon_coords))
        # Make a bedtool for each intron
        self.intron_bedtools = \
            map(lambda x: self.coords_to_intron_bedtool(self.exon_coords, x),
                range(1, self.n_exons))

        if genome_fasta is not None:
            self.exon_fastas = map(lambda x: x.sequence(fi=self.genome_fasta,
                                                        s=True).seqfn,
                                  self.exon_bedtools)
            self.intron_fastas = map(lambda x:
                                     x.sequence(fi=self.genome_fasta,
                                                s=True).seqfn,
                                     self.intron_bedtools)


    def miso_exon_to_gencode_exon(self, exon):
        """Convert a single miso exon to one or more gffutils database exon id

        >>> # Skipped exon (SE) or Mutually exclusive exon (MXE) ID
        >>> miso_exon_to_gencode_exon('chr2:9624561:9624679:+')
        'exon:chr2:9624561-9624679:+'
        >>> # Alt 5' splice site (pick first of alternative)
        >>> miso_exon_to_gencode_exon('chr15:42565276:42565087|42565161:-')
        'exon:chr15:42565276-42565087:-'
        >>> # Alt 3' splice site (pick first of alternative)
        >>> miso_exon_to_gencode_exon('chr2:130914199|130914248:130914158:-')
        'exon:chr2:130914199-130914158:-'
        >>> # Retained intron: start, stop separated by '-' instead of ':'
        >>> miso_exon_to_gencode_exon('chr1:906259-906386:+')
        'exon:chr1:906259-906386:+'
        """
        return 'exon:{}:{}-{}:{}'.format(*self.miso_exon_to_coords(exon))


    def miso_id_to_exon_ids(self, miso_id):
        """Convert a MISO-style alternative event ID to a gffutils exon id of
        all exons in all possible transcripts

        Split on the pipe ("|") to account for Alt 5'/3' splice site events

        # A skipped exon (SE) ID
        >>> miso_id_to_exon_ids('chr2:9624561:9624679:+@chr2:9627585:9627676:+@chr2:9628276:9628591:+')
        ['exon:chr2:9624561-9624679:+', 'exon:chr2:9627585-9627676:+', 'exon:chr2:9628276-9628591:+']
        >>> # A mutually exclusive (MXE) ID
        >>> miso_id_to_exon_ids('chr16:89288500:89288591:+@chr16:89289565:89289691:+@chr16:89291127:89291210:+@chr16:89291963:89292039:+')
        ['exon:chr16:89288500-89288591:+', 'exon:chr16:89289565-89289691:+', 'exon:chr16:89291127-89291210:+', 'exon:chr16:89291963-89292039:+']
        >>> # An Alt 5' splice site (A5SS) ID
        >>> miso_id_to_exon_ids("chr15:42565276:42565087|42565161:-@chr15:42564261:42564321:-")
        ['exon:chr15:42565276-42565161:-', 'exon:chr15:42564261-42564321:-']
        >>> # An Alt 3' splice site (A3SS) ID
        >>> miso_id_to_exon_ids('chr2:130914824:130914969:-@chr2:130914199|130914248:130914158:-')
        ['exon:chr2:130914824-130914969:-', 'exon:chr2:130914199-130914158:-']
        >>> # A retained intron (RI) ID
        >>> miso_id_to_exon_ids('chr1:906066-906138:+@chr1:906259-906386:+')
        ['exon:chr1:906066-906138:+', 'exon:chr1:906259-906386:+', 'exon:chr1:906066-906386:+']
        """
        return map(self.miso_exon_to_gencode_exon, miso_id.split('@'))


    def miso_exon_to_coords(self, exon):
        """Convert a miso exon to gffutils coordinates

        >>> miso_exon_to_coords('chr2:130914824:130914969:-')
        ('chr2', '130914824', '130914969', '-')
        >>> # Alt 5' SS - pick the first of the alternative ends
        >>> miso_exon_to_coords('chr15:42565276:42565087|42565161:-')
        ('chr15', '42565276', '42565087', '-')
        >>> # Alt 3' SS - pick the first of the alternative starts
        >>> miso_exon_to_coords('chr2:130914199|130914248:130914158:-')
        ('chr2', '130914199', '130914158', '-')
        >>> # Retained intron
        >>> miso_exon_to_coords('chr1:906066-906138:+')
        ('chr1', '906066', '906138', '+')
        """
        strand = exon[-1]
        coords = map(lambda x: x.split('|')[0],
                     exon.split(':'))
        if '-' in coords[1]:
            start, stop = coords[1].split('-')
            coords = coords[0], start, stop, strand
        return coords[0], coords[1], coords[2], strand

    def coords_to_bedtool(self, single_exon_coords):
        """Convert exon coordinates to bedtool intervals

        Assumes that the coordinates are in the exact same order as the
        original miso ids.

        Parameters
        ----------
        single_exon_coords : list
            List of (chrom, start, stop, strand) tuples of a single exon's
            coordinates

        Returns
        -------
        bedtool : pybedtools.BedTool
            A bedtool object of the exon intervals
        """
        if len(single_exon_coords) != len(self.miso_ids):
            raise ValueError("Number of coordinates must equal the number of "
                             "original miso ids")
        intervals = []
        for miso_id, exon in zip(self.miso_ids, single_exon_coords):
            chrom, start, stop, strand = exon

            # Base-0-ify
            start = int(start) - 1
            stop = int(stop)

            intervals.append(
                pybedtools.Interval(chrom, start, stop, strand=strand,
                                    name=miso_id, score='1000'))
        return pybedtools.BedTool(intervals)

    def coords_to_intron_bedtool(self, coords, intron_number):
        """Convert exon coordinates to bedtool intervals of the introns

        Parameters
        ----------
        coords : list
            List of tuples of exons and their chrom-start-stop-strand,
            coordinates, e.g. an example of the first item is,

            (("chr1", "100", "200", "+"), ("chr1", "300", "400", "+"),
             ("chr1", "500", "600", "+"))

        Returns
        -------
        bedtool : pybedtools.BedTool
            A bedtool object of the intron intervals

        >>> coords = [(("chr1", "100", "200", "+"), ("chr1", "300", "400",
        ...                                          "+"),
        ...            ("chr1", "500", "600", "+")),
        ...           (("chr2", "500", "600", "-"), ("chr2", "300", "400",
        ...                                          "-"),
        ...            ("chr2", "100", "200", "-"))]
        >>> bt = self.coords_to_intron_bedtool(coords, 1)
        >>> print(bt)
        chr1    200 300 1000    +
        chr2    400 500 1000    -
        """
        intervals = []
        for miso_id, exons in zip(self.miso_ids, coords):
            exon1, exon2 = exons[(intron_number - 1):(intron_number + 1)]
            chrom1, start1, stop1, strand1 = exon1
            chrom2, start2, stop2, strand2 = exon2

            chrom = chrom1
            strand = strand1
            if strand == '+':
                start = stop1
                stop = start2
            elif strand == '-':
                start = stop2
                stop = start1

            # Base-0-ify, and stop is non-inclusive in beds
            start = int(start) - 1
            stop = int(stop)

            if start > stop:
                start_original = start
                stop_original = stop
                start = stop_original
                stop = start_original

            intervals.append(
                pybedtools.Interval(chrom, start, stop, strand=strand,
                                    name=miso_id, score='1000'))

        return pybedtools.BedTool(intervals)

    def convert_miso_ids_to_everything(self, miso_ids, db,
                                       event_type,
                                       out_dir):
        """Given a list of miso IDs and a gffutils database, pull out the
        ensembl/gencode/gene name/gene type/transcript names, and write files
        into the out directory. Does not return a value.

        Parameters
        ----------
        miso_ids : list of str
            Miso ids to convert
        db : gffutils.FeatureDB
            gffutils feature database created from a gtf file
        event_type : str
            The type of splicing event. This is used for naming only
        out_dir : str
            Where to write the files to.
        """
        out_dir = out_dir.rstrip('/')
        event_type = event_type.lower()

        miso_to_ensembl = {}
        miso_to_gencode = {}
        miso_to_gene_name = {}
        miso_to_gene_type = {}
        miso_to_ensembl_transcript = {}
        miso_to_gencode_transcript = {}

        ensembl_to_miso = defaultdict(list)
        gencode_to_miso = defaultdict(list)
        gene_name_to_miso = defaultdict(list)

        n_miso_ids = len(miso_ids)
        sys.stdout.write(
            'Converting {} {} miso ids using {} gffutils database '
            'into {}.'.format(n_miso_ids, event_type, str(db),
                              out_dir))

        for i, miso_id in enumerate(miso_ids):
            if i % 100 == 0:
                sys.stdout.write('On {}/{} {} miso ids'.format(i, n_miso_ids,
                                                               event_type))

            exons = self.miso_id_to_exon_ids(miso_id)

            gencode = set([])
            ensembl = set([])
            gene_name = set([])
            gene_type = set([])
            gencode_transcript = set([])
            ensembl_transcript = set([])
            for e in exons:
                try:
                    exon = db[e]
                    gencode.update(exon.attributes['gene_id'])
                    ensembl.update(
                        map(lambda x: x.split('.')[0],
                            exon.attributes['gene_id']))
                    gene_name.update(exon.attributes['gene_name'])
                    gene_type.update(exon.attributes['gene_type'])
                    gencode_transcript.update(exon.attributes['transcript_id'])
                    ensembl_transcript.update(
                        map(lambda x: x.split('.')[0], exon.attributes[
                            'transcript_id']))
                except gffutils.FeatureNotFoundError:
                    try:
                        # not an exon, look for any overlapping transcripts here
                        prefix, chrom, startstop, strand = e.split(':')
                        start, stop = startstop.split('-')
                        transcripts = list(db.features_of_type('transcript',
                                                               strand=strand,
                                                               limit=(
                                                                   chrom,
                                                                   int(start),
                                                                   int(stop))))
                        # if there are overlapping transcripts...
                        if len(transcripts) == 0:
                            continue
                        else:
                            for transcript in transcripts:
                                gencode.update(
                                    transcript.attributes['gene_id'])
                                ensembl.update(
                                    map(lambda x: x.split('.')[0],
                                        transcript.attributes['gene_id']))
                                gene_name.update(
                                    transcript.attributes['gene_name'])
                                gene_type.update(
                                    transcript.attributes['gene_type'])
                                gencode_transcript.update(
                                    transcript.attributes['transcript_id'])
                                ensembl_transcript.update(
                                    map(lambda x: x.split('.')[0],
                                        transcript.attributes[
                                            'transcript_id']))
                    except:
                        continue
            if len(gencode) > 0:

                for ens in ensembl:
                    ensembl_to_miso[ens].append(miso_id)
                for g in gene_name:
                    gene_name_to_miso[g].append(miso_id)
                for g in gencode:
                    gencode_to_miso[g].append(miso_id)

                ensembl = ','.join(ensembl)

                gencode = ','.join(gencode)
                gene_name = ','.join(gene_name)
                gene_type = ','.join(gene_type)

                gencode_transcript = ','.join(gencode_transcript)
                ensembl_transcript = ','.join(ensembl_transcript)

                miso_to_gencode[miso_id] = gencode
                miso_to_ensembl[miso_id] = ensembl
                miso_to_gene_name[miso_id] = gene_name
                miso_to_gene_type[miso_id] = gene_type

                miso_to_gencode_transcript[miso_id] = gencode_transcript
                miso_to_ensembl_transcript[miso_id] = ensembl_transcript

            else:
                miso_to_gencode[miso_id] = np.nan
                miso_to_ensembl[miso_id] = np.nan
                miso_to_gene_name[miso_id] = np.nan
                miso_to_gene_type[miso_id] = np.nan

        miso_tos = {'gencode_gene': miso_to_gencode,
                    'ensembl_gene': miso_to_ensembl,
                    'gene_name': miso_to_gene_name,
                    'gene_type': miso_to_gene_type,
                    'gencode_transcript': miso_to_gencode_transcript,
                    'ensembl_transcript': miso_to_ensembl_transcript}

        to_misos = {'ensembl_gene': ensembl_to_miso,
                    'gene_name': gene_name_to_miso,
                    'gencode_gene': gencode_to_miso}

        for name, d in miso_tos.iteritems():
            df = pd.DataFrame.from_dict(d, orient='index')
            df.index.name = 'event_name'
            df.columns = [name]
            tsv = '{}/miso_{}_to_{}.tsv'.format(out_dir, event_type, name)
            df.to_csv(tsv, sep='\t')
            sys.stdout.write('Wrote {}\n'.format(tsv))

        for name, d in to_misos.iteritems():
            tsv = '{}/{}_to_miso_{}.tsv'.format(out_dir, name, event_type)
            with open(tsv, 'w') as f:
                for k, v in d.iteritems():
                    f.write('{}\t{}\n'.format(k, '\t'.join(v)))
            sys.stdout.write('Wrote {}\n'.format(tsv))


            # if isoform == 1:
            # return isoform1
            # elif isoform == 2:
            # return isoform2


    def splice_type_isoforms(self, splice_type, transcripts):
        """Get transcripts corresponding to isoform1 or isoform2 of a splice type

        Parameters
        ----------

        transcripts : list
            List of gffutils iterators of transcripts, where the position indicates
            the exon for which this transcript corresponds.
            For example, for SE events:
            [transcripts_containing_exon1, transcripts_containing_exon2,
            transcripts_containing_exon3]

        Returns
        -------
        isoform1 : set
            Set of transcripts corresponding to isoform1
        isoform2 : set
            Set of transcripts corresponding to isoform2

        Raises
        ------
        """
        isoform1s, isoform2s = None, None
        if splice_type == 'SE':
            isoform1s = set(transcripts[0]) & set(transcripts[2])
            isoform2s = set(transcripts[1]) & isoform1s
        elif splice_type == 'MXE':
            # Isoform 1 is inclusion of the far, second alternative exon
            isoform1s = set(transcripts[0]) & set(transcripts[2]) & \
                        set(transcripts[3])
            # Isoform 2 is inclusion of the near, first alternative exon
            isoform2s = set(transcripts[0]) & set(transcripts[1]) & \
                        set(transcripts[3])
        return isoform1s, isoform2s


    def seq_name_to_exon_id(self, seq_name):
        chr_start_stop, strand = seq_name.split('(')
        chrom, startstop = chr_start_stop.split(':')
        start, stop = startstop.split('-')
        start = int(start) + 1
        chr_start_stop = '{}:{}-{}'.format(chrom, start, stop)
        strand = strand.rstrip(')')
        exon = 'exon:{}:{}'.format(chr_start_stop, strand)
        return exon


    def isoform_sequences(self, splice_type, miso_ids):
        """Get the mRNA sequences of the splicing events

        Parameters
        ----------
        splice_type : 'SE' | 'MXE' | 'A5SS' | 'A3SS' | 'RI'
            Name of the type of splicing event
        miso_ids : list-like
            List of miso ID strings, where each exon is delimited by @,
            e.g. for SE: chr1:100:200:+@chr1:300:350:+@chr1:400:500:+


        Returns
        -------


        Raises
        ------
        """
        pass


    def isoform_translations(self, gffdb):
        """Get the protein translations (when possible) of the splicing events

         Uses the gffdb to check whether the exons from the miso ID correspond
         to a coding sequence (CDS)

        Parameters
        ----------
        splice_type : 'SE' | 'MXE' | 'A5SS' | 'A3SS' | 'RI'
            Name of the type of splicing event
        miso_ids : list-like
            List of miso ID strings, where each exon is delimited by @,
            e.g. for SE: chr1:100:200:+@chr1:300:350:+@chr1:400:500:+
        gffdb : gffutils.FeatureDB
            A gffutils database, which must have been created by
            create_gffutils_db.create_db because it requires that coding sequences
            aka CDS's are stored like CDS:chr1:100-200:2 where the last 2 is the
            frame of the CDS

        Returns
        -------


        Raises
        ------

        """
        isoform_seqs = defaultdict(list)
        isoform_translations = defaultdict(lambda: defaultdict(list))

        for exon_ids, miso_id in zip(self.exon_ids, self.miso_ids):
            # print seq1.name

            try:
                # print seq1.name, exon_id1
                exons = [gffdb[exon_id] for exon_id in exon_ids]
            except gffutils.FeatureNotFoundError:
                continue
                # print exon1
                #         print

            cds_ids = map(lambda x: ':'.join(['CDS', x.split('exon:')[1]]),
                          exon_ids)
            transcripts = [gffdb.parents(e, featuretype='transcript')
                           for e in exons]
            transcripts_per_isoform = self.splice_type_isoforms(
                self.splice_type,
                transcripts)
            cds_ids_per_isoform = self.splice_type_exons(self.splice_type,
                                                         cds_ids)

            # Remove all overlapping isoforms
            intersect = transcripts_per_isoform[0].intersection(
                transcripts_per_isoform[1])
            isoform1s = transcripts_per_isoform[0].difference(intersect)
            isoform2s = transcripts_per_isoform[1].difference(intersect)
            transcripts_per_isoform = isoform1s, isoform2s


            # exon_seqs_per_isoform = self.splice_type_exons(exon_seqs)

            for i, (transcripts, cds_isoform) in enumerate(
                    zip(transcripts_per_isoform, cds_ids_per_isoform)):
                event_isoform = '{}_isoform{}'.format(miso_id, i + 1)
                # Check if this is the reversed strand
                reverse = exon_ids[0][-1] == '-'

                for t in transcripts:
                    name = '{}_{}'.format(event_isoform, t.id)
                    cds = list(gffdb.children(t, featuretype='CDS',
                                              reverse=reverse,
                                              order_by='start'))
                    cds_in_splice_form = [(i, c) for c in cds if
                                          sum(map(lambda x: x.startswith(c.id),
                                                  cds_isoform)) > 0]
                    correct_number_of_cds = \
                        len(cds_in_splice_form) == len(cds_isoform)
                    cds_in_correct_order = True
                    for (i, c), (j, d) in zip(cds_in_splice_form,
                                              cds_in_splice_form[1:]):
                        if i + 1 != j:
                            cds_in_correct_order = False
                    if cds_in_correct_order and correct_number_of_cds:
                        frame = cds[0].frame
                        cds_seqs = [c.sequence(self.genome_fasta,
                                               use_strand=True) for c in cds]
                        seq = ''.join(s.seq for s in cds_seqs)
                        seq_translated = seq[int(frame):].translate()
                        if seq_translated in isoform_translations[i]:
                            continue
                        seqrecord = SeqRecord(seq_translated, id=name,
                                              description='')
                        isoform_seqs[i].append(seqrecord)
                        isoform_translations[i][t].append(seq_translated)
                    else:
                        isoform_translations[i][t].append('no translation')

        return isoform_seqs, isoform_translations


    def splice_type_exons(self, splice_type, exons):
        """Get exons corresponding to a particular isoform of a splice type

        For SE:
            isoform1: exon1, exon2
            isoform2: exon1, exon2, exon3
        for MXE:
            isoform1: exon1, exon3, exon4
            isoform2: exon1, exon2, exon4

        Parameters
        ----------
        splice_type : 'SE' | 'MXE'
            String specifying the splice type. Currently only SE (skipped exon) and
            MXE (mutually exclusive exon) are supported
        exons : list
            List of exons or CDS's (ids, strings, you name it) in the exact order
            of the splice type, e.g. (exon1_id, exon2_id, exon3_id) for SE

        Returns
        -------
        isoform1_exons : tuple
            Tuple of exons corresponding to isoform 1
        isoform2_exons : tuple
            Tuple of exons corresponding to isoform 2
        """
        isoform1, isoform2 = None, None
        if splice_type == 'SE':
            isoform1 = exons[0], exons[2]
            isoform2 = exons[0], exons[1], exons[2]
        elif splice_type == 'MXE':
            isoform1 = exons[0], exons[1], exons[3]
            isoform2 = exons[0], exons[2], exons[3]
        return isoform1, isoform2




def write_sashimi_plot_settings(filename,  bam_prefix, miso_prefix,
                                bam_files, miso_files, mapped_reads,
                               colors, splice_type='SE',
                               fig_width=7, fig_height=5, intron_scale=1,
                               exon_scale=1, logged=False, font_size=6,
                                ymax=150,
                               show_posteriors=True,
                               bar_posteriors=True,
                               number_junctions=True,
                               resolution=0.5,
                               posterior_bins=40,
                               gene_posterior_ratio=5,
                               bar_color='#4c72b0',
                               bf_thresholds=(0, 1, 2, 5, 10, 20),
                               sample_labels=None, reverse_minus=False):
    """Write a settings file for making publication-quality Sashimi plots of exon junctions

    Note that the lists provided, ``bam_files``, ``miso_files``, ``mapped_reads`` and ``colors``
    must all be in the exact same sample order. This is not checked, but you will get weird
    plots if you do otherwise.

    Parameters
    ----------
    filename : str
        Name of the file to write
    bam_prefix : str
        Directory where BAM files are
    miso_prefix : str
        Directory where MISO output is
    bam_files : list
        List of bam file locations
    miso_files : list
        List of miso output locations (Samples must be in same order as bam files)
    colors : list, optional
        List of hexadecimal colors to use, e.g. ['#55a868', '#55a868']
    fig_width : int, optional
        Width of the figure, in inches
    fig_height : int, optional
        Height of the figure, in inches
    intron_scale : float, optional
        Factor to scale down the introns by in the figure
    exon_scale : float, optional
        Factor to scale down exons by in th efigure
    logged : bool, optional
        If True, use a log-scale when plotting
    font_size : float, optional
        Size of the text on the plot
    ymax : float, optional
        Maximum value of the y-axis
    show_posteriors : bool, optional
        If True, plot posterior distributions inferred by MISO
    bar_posteriors : bool, optional
        If True, plot posterior distributions as bar graphs
    number_junctions : bool, optional
        If True, plot the number of reads in each junction
    resolution : float, optional
        Resolution of the figure? Unclear
    posterior_bins : int, optional
        Number of bins to plot posterior distribution
    gene_posterior_ratio : int, optional
        ??
    bar_color : color, optional
        Color of the bar plots for Bayes factor distribution (default Seaborn blue)
    bf_thresholds : list, optional
        Bayes factors thresholds to use for --plot-bf-dist
    reverse_minus : bool, optional
        If True, "-" strand events will go from left to right instead of right to left
    """
    sys.stdout.write('Writing Sashimi plot settings to {0} ...\n'.format(filename))

    if colors is not None:
        colors_list = '[{0}]'.format(',\n\t'.join(map(lambda x: '"{0}"'.format(x), colors)))
        colors = 'colors = {0}'.format(colors_list)
    else:
        colors = ''
    if sample_labels is not None:
        sample_labels_list = '[{0}]'.format(',\n\t'.join(map(lambda x: '"{0}"'.format(x), sample_labels)))
        sample_labels = 'sample_labels = {0}'.format(sample_labels_list)
    else:
        sample_labels = ''

    with open(filename, 'w') as f:
        f.write("""
[data]
# directory where BAM files are
bam_prefix = {0}
# directory where MISO output is
miso_prefix = {1}

bam_files = {2}
miso_files = {3}

[plotting]
# Dimensions of figure to be plotted (in inches)
fig_width = {4}
fig_height = {5}
# Factor to scale down introns and exons by
intron_scale = {6}
exon_scale = {7}
# Whether to use a log scale or not when plotting
logged = {8}
font_size = {9}

# Max y-axis
ymax = {10}

# Whether to plot posterior distributions inferred by MISO
show_posteriors = {11}

# Whether to show posterior distributions as bar summaries
bar_posteriors = {12}

# Whether to plot the number of reads in each junction
number_junctions = {13}

resolution = {14}
posterior_bins = {15}
gene_posterior_ratio = {16}

# List of colors for read denisites of each sample
{17}

# Number of mapped reads in each sample
# (Used to normalize the read density for RPKM calculation)
coverages = {18}

# Bar color for Bayes factor distribution
# plots (--plot-bf-dist)
# Paint them Seaborn "deep" palette blue
bar_color = "{19}"

# Bayes factors thresholds to use for --plot-bf-dist
bf_thresholds = {20}

# Renamed sample ids
{21}

# Whether or not to reverse the minus strand events
reverse_minus = {22}

""".format(
                bam_prefix, miso_prefix,
        '[{0}]'.format(',\n\t'.join(map(lambda x: '"{0}"'.format(x), bam_files))),
        '[{0}]'.format(',\n\t'.join(map(lambda x: '"{0}/{1}"'.format(x, splice_type), miso_files))),
                fig_width, fig_height, intron_scale, exon_scale, logged, font_size, ymax, show_posteriors,
                bar_posteriors, number_junctions, resolution, posterior_bins, gene_posterior_ratio,
                colors,
                '[{0}]'.format(',\n\t'.join(map(lambda x: '{0}'.format(int(x)), mapped_reads))),
                bar_color, bf_thresholds,
                sample_labels, reverse_minus
        ))
    sys.stdout.write('\tDone.')