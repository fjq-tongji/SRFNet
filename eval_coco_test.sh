python eval.py --dump_images 0\
	--num_images 5000  \
        --input_json data/coco/cocotest.json  \
        --input_fc_dir data/coco/cocotest_bu_fc  \
	--input_att_dir data/coco/cocotest_bu_att \
	--input_label_h5 none \
	--num_images -1 \
        --infos_path log/log_transformer/infos_transformer.pkl \
	--model log/log_transformer/model.pth \
	--language_eval 0  \
	--save_path_seq log/eval_coco_test_seq.json \
        --split test
