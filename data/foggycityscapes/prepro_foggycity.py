"""
Preprocess a raw json dataset into hdf5/json files for use in data_loader.lua
Input: json file that has the form
[{ file_path: 'path/img.jpg', captions: ['a caption', ...] }, ...]
example element in this list would look like
{'captions': [u'A man with a red helmet on a small moped on a dirt road. ', u'Man riding a motor bike on a dirt road on the countryside.', u'A man riding on the back of a motorcycle.', u'A dirt path with a young person on a motor bike rests to the foreground of a verdant area with a bridge and a background of cloud-wreathed mountains. ', u'A man in a red shirt and a red hat is on a motorcycle on a hill side.'], 'file_path': u'val2014/COCO_val2014_000000391895.jpg', 'id': 391895}
This script reads this json, does some basic preprocessing on the captions
(e.g. lowercase, etc.), creates a special UNK token, and encodes everything to arrays
Output: a json file and an hdf5 file
The hdf5 file contains several fields:
/images is (N,3,256,256) uint8 array of raw image data in RGB format
/labels is (M,max_length) uint32 array of encoded labels, zero padded
/label_start_ix and /label_end_ix are (N,) uint32 arrays of pointers to the
  first and last indices (in range 1..M) of labels for each image
/label_length stores the length of the sequence for each of the M sequences
The json file has a dict that contains:
- an 'ix_to_word' field storing the vocab in form {ix:'word'}, where ix is 1-indexed
- an 'images' field that is a list holding auxiliary information for each image,
  such as in particular the 'split' it was assigned to.
"""

import os
import json
import argparse
from random import shuffle, seed
import string
# non-standard dependencies:
import h5py
import numpy as np
from imageio import imread
#from scipy import imresize
from PIL import Image


def prepro_captions(imgs):
    # preprocess all the captions
    print('example processed tokens:')
    for i, img in enumerate(imgs):
        img['processed_tokens'] = []
        for j, s in enumerate(img['captions']):   ################################
            txt = str(s).lower().translate(string.punctuation).strip().split()
            img['processed_tokens'].append(txt)
            if i < 10 and j == 0:
                print(txt)


def build_vocab(imgs, params):
    count_thr = params['word_count_threshold']

    # count up the number of words
    counts = {}
    for img in imgs:
        for txt in img['processed_tokens']:
            for w in txt:
                counts[w] = counts.get(w, 0) + 1
    cw = sorted([(count, w) for w, count in counts.items()], reverse=True)
    print('top words and their counts:')
    print('\n'.join(map(str, cw[:20])))

    # print some stats
    total_words = sum(counts.values())
    print('total words:', total_words)
    bad_words = [w for w, n in counts.items() if n <= count_thr]
    vocab = [w for w, n in counts.items() if n > count_thr]
    bad_count = sum(counts[w] for w in bad_words)
    print('number of bad words: %d/%d = %.2f%%' % (len(bad_words), len(counts), len(bad_words) * 100.0 / len(counts)))
    print('number of words in vocab would be %d' % (len(vocab),))
    print('number of UNKs: %d/%d = %.2f%%' % (bad_count, total_words, bad_count * 100.0 / total_words))

    # lets look at the distribution of lengths as well
    sent_lengths = {}
    for img in imgs:
        for txt in img['processed_tokens']:
            nw = len(txt)
            sent_lengths[nw] = sent_lengths.get(nw, 0) + 1
    max_len = max(sent_lengths.keys())
    print('max length sentence in raw data: ', max_len)
    print('sentence length distribution (count, number of words):')
    sum_len = sum(sent_lengths.values())
    for i in range(max_len + 1):
        print('%2d: %10d   %f%%' % (i, sent_lengths.get(i, 0), sent_lengths.get(i, 0) * 100.0 / sum_len))

    # lets now produce the final annotations
    if bad_count > 0:
        # additional special UNK token we will use below to map infrequent words to
        print('inserting the special UNK token')
        vocab.append('UNK')

    for img in imgs:
        img['final_captions'] = []
        for txt in img['processed_tokens']:
            caption = [w if counts.get(w, 0) > count_thr else 'UNK' for w in txt]
            img['final_captions'].append(caption)
    print(vocab)

    return vocab


def assign_splits(imgs, params):
    num_val = params['num_val']
    num_test = params['num_test']

    for i, img in enumerate(imgs):
        if i < num_val:
            img['split'] = 'val'
        elif i < num_val + num_test:
            img['split'] = 'test'
        else:
            img['split'] = 'train'

    print('assigned %d to val, %d to test.' % (num_val, num_test))


def encode_captions(imgs, params, wtoi):
    """
    encode all captions into one large array, which will be 1-indexed.
    also produces label_start_ix and label_end_ix which store 1-indexed
    and inclusive (Lua-style) pointers to the first and last caption for
    each image in the dataset.
    """

    max_length = params['max_length']
    N = len(imgs)
    M = sum(len(img['final_captions']) for img in imgs)  # total number of captions

    label_arrays = []
    label_start_ix = np.zeros(N, dtype='uint32')  # note: these will be one-indexed
    label_end_ix = np.zeros(N, dtype='uint32')
    label_length = np.zeros(M, dtype='uint32')
    caption_counter = 0
    counter = 1
    for i, img in enumerate(imgs):
        n = len(img['final_captions'])
        assert n > 0, 'error: some image has no captions'

        Li = np.zeros((n, max_length), dtype='uint32')
        for j, s in enumerate(img['final_captions']):
            label_length[caption_counter] = min(max_length, len(s))  # record the length of this sequence
            caption_counter += 1
            for k, w in enumerate(s):
                if k < max_length:
                    Li[j, k] = wtoi[w]

        # note: word indices are 1-indexed, and captions are padded with zeros
        label_arrays.append(Li)
        label_start_ix[i] = counter
        label_end_ix[i] = counter + n - 1

        counter += n

    L = np.concatenate(label_arrays, axis=0)  # put all the labels together
    print('$$$$$$$$$$$$$$$$$')
    print(L.shape)
    assert L.shape[0] == M, 'lengths don\'t match? that\'s weird'
    #assert np.all(label_length > 0), 'error: some caption had no words?'

    #print('encoded captions to array of size ', `L.shape`)
    return L, label_start_ix, label_end_ix, label_length


def main(params):
    imgs = json.load(open(params['input_json'], 'r'))
    seed(123)  # make reproducible
    shuffle(imgs)  # shuffle the order

    # tokenization and preprocessing
    prepro_captions(imgs)

    # create the vocab
    vocab = build_vocab(imgs, params)
    itow = {i + 1: w for i, w in enumerate(vocab)}  # a 1-indexed vocab translation table
    wtoi = {w: i + 1 for i, w in enumerate(vocab)}  # inverse table
    print(wtoi)

    # assign the splits #######################################################################
    #assign_splits(imgs, params)

    # encode captions in large arrays, ready to ship to hdf5 file
    L, label_start_ix, label_end_ix, label_length = encode_captions(imgs, params, wtoi)

    # create output h5 file
    N = len(imgs)
    f = h5py.File(params['output_h5'], "w")
    f.create_dataset("labels", dtype='uint32', data=L)
    f.create_dataset("label_start_ix", dtype='uint32', data=label_start_ix)
    f.create_dataset("label_end_ix", dtype='uint32', data=label_end_ix)
    f.create_dataset("label_length", dtype='uint32', data=label_length)
    #dset = f.create_dataset("images", (N, 3, 256, 256), dtype='uint8')  # space for resized images
    #for i, img in enumerate(imgs):
        # load the image
        #I = os.path.join(params['images_root'], img['file_path'])
        #print(I)
        #Ir = im.resize(I, (256, 256))

        #Ir = Image.open(I)
        #Ir = Ir.resize((256, 256), Image.ANTIALIAS)
        #print(Ir)


        #Ir = np.array(Image.fromarray(I).resize((256, 256)))

        #except:
         #   print('failed resizing image %s - see http://git.io/vBIE0' % (img['file_path'],))
          #  raise
        # handle grayscale input images
        #if len(Ir.shape) == 2:
         #   Ir = Ir[:, :, np.newaxis]
          #  Ir = np.concatenate((Ir, Ir, Ir), axis=2)
        # and swap order of axes from (256,256,3) to (3,256,256)
        #Ir = Ir.transpose(1, 2)
        #Ir = Ir.transpose(0, 1)

        # write to h5
        #dset[i] = Ir
        #if i % 1000 == 0:
         #   print('processing %d/%d (%.2f%% done)' % (i, N, i * 100.0 / N))
    f.close()
    print('wrote ', params['output_h5'])

    # create output json file
    out = {}
    out['ix_to_word'] = itow  # encode the (1-indexed) vocab
    out['images'] = []
    for i, img in enumerate(imgs):

        jimg = {}
        jimg['split'] = img['split']
        if 'file_path' in img: jimg['file_path'] = img['file_path']  # copy it over, might need
        if 'id' in img: jimg['id'] = img['id']  # copy over & mantain an id, if present (e.g. coco ids, useful)

        out['images'].append(jimg)

    json.dump(out, open(params['output_json'], 'w'))
    print('wrote ', params['output_json'])


############################################# object_name word2num
    from os import listdir
    import torch
    # files = []
    # files_ = [f for f in listdir(params['object_name'])]
    # for i in files_:
    #     if i[-3:] == 'npy':
    #         files.append(i)
    # print(files)
    # for file_npy in files:
    #     _new_list = []
    #     word_list = np.load(os.path.join(params['object_name'], file_npy))   ## list word
    #     for i in word_list:
    #         if i in wtoi:
    #             j = wtoi[i]
    #             _new_list.append(j)
    #             print('$$$$$$$$$$')
    #
    #         else:
    #             k = wtoi['UNK']
    #             _new_list.append(k)
    #     np.save(os.path.join(params['object_name_num'], file_npy), _new_list)

            
############################################# attr_name word2num
    # files = []
    # files_ = [f for f in listdir(params['attr_name'])]
    # for i in files_:
    #     if i[-3:] == 'npy':
    #         files.append(i)
    # #print(files)
    # for file_npy in files:
    #     _new_list = []
    #     word_list = np.load(os.path.join(params['attr_name'], file_npy))  ## list word
    #     for i in word_list:
    #         if i in wtoi:
    #             j = wtoi[i]
    #             _new_list.append(j)
    #             print('$$$$$$$$$$')
    #
    #         else:
    #             k = wtoi['UNK']
    #             _new_list.append(k)
    #     np.save(os.path.join(params['attr_name_num'], file_npy), _new_list)


############################################# seg_name word2num
    files = []
    files_ = [f for f in listdir(params['seg_name'])]
    for i in files_:
        if i[-3:] == 'npy':
            files.append(i)
    for file_npy in files:
        _new_list = []
        word_list = np.load(os.path.join(params['seg_name'], file_npy))  ## list word
        for i in word_list:
            if i in wtoi:
                j = wtoi[i]
                _new_list.append(j)
                print('$$$$$$$$$$')

            else:
                k = wtoi['UNK']
                _new_list.append(k)
        np.save(os.path.join(params['seg_name_num'], file_npy), _new_list)






if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # input json
    parser.add_argument('--input_json', default=r'FoggyCityscapes_paragraph_trainval.json',
                        help='input json file to process into hdf5')
    parser.add_argument('--num_val', default=0,  type=int,
                        help='number of images to assign to validation data (for CV etc)')
    parser.add_argument('--output_json', default=r'FoggyCityscapes_paragraph_trainval_talk---new.json',
                        help='output json file')
    parser.add_argument('--output_h5', default=r'FoggyCityscapes_paragraph_trainval_talk---new.h5',
                        help='output h5 file')
    parser.add_argument('--object_name', default='/data2/fjq/1.next_paper/foggyCityscapes-total/3.foggycity_label_filter_0.2',
                        help='')
    parser.add_argument('--object_name_num', default='/data2/fjq/1.next_paper/foggyCityscapes-total/3.foggycity_label_filter_0.2/num',
                       help='')

    parser.add_argument('--attr_name', default='/data2/fjq/1.next_paper/foggyCityscapes-total/4.foggycity_attr_filter_1.0',
                        help='')
    parser.add_argument('--attr_name_num', default='/data2/fjq/1.next_paper/foggyCityscapes-total/4.foggycity_attr_filter_1.0/num',
                        help='')

    parser.add_argument('--seg_name', default='seg_name',
                        help='')
    parser.add_argument('--seg_name_num', default='seg_name/num',
                        help='')




    # options
    parser.add_argument('--max_length', default=80, type=int,
                        help='max length of a caption, in number of words. captions longer than this get clipped.')
    parser.add_argument('--images_root',
                        default=r'../cityscapes_image_caption/cityscapes',
                        help='root location in which images are stored, to be prepended to file_path in input json')
    parser.add_argument('--word_count_threshold', default=5, type=int,
                        help='only words that occur more than this number of times will be put in vocab')
    parser.add_argument('--num_test', default=1525, type=int,
                        help='number of test images (to withold until very very end)')

    args = parser.parse_args()
    params = vars(args)  # convert to ordinary dict
    print('parsed input parameters:')
    print(json.dumps(params, indent=2))
    main(params)
