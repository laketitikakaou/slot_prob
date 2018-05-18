import time
from abc import ABCMeta, abstractmethod

import numpy as np
import tensorflow as tf

from .utils import (get_sample_num, new_variable_initializer,
                    sigmoid_cross_entropy_with_probs, sklearn_shuffle,
                    sklearn_split)


class TFBaseModel(metaclass=ABCMeta):
    def __init__(self, seed=1024, checkpoint_path=None):
        self.seed = seed

        if checkpoint_path and checkpoint_path.count('/') < 2:
            raise ValueError('checkpoint_path must be dir/model_name format')
        self.checkpoint_path = checkpoint_path
        self.train_flag = True
        self.graph = tf.Graph()
        self.sess = tf.Session(graph=self.graph, config=tf.ConfigProto(
            allow_soft_placement=True, log_device_placement=False))

        with self.graph.as_default():
            tf.set_random_seed(self.seed)  # 设置随机种子
            self.global_step = tf.Variable(
                0, dtype=tf.int32, trainable=False, name='global_step')
            self.sample_weight = tf.placeholder(
                tf.float32, shape=[None, ], name='sample_weight')

    def get_variable(self, variable, feed_dict=None):
        return self.sess.run([variable], feed_dict)

    @abstractmethod
    def _get_input_data(self, ):
        raise NotImplementedError

    @abstractmethod
    def _get_input_target(self, ):
        raise NotImplementedError

    @abstractmethod
    def _get_output_target(self, ):
        raise NotImplementedError

    def _compute_sample_weight(self, labels, class_weight=None, sample_weight=None):
        if class_weight is None and sample_weight is None:
            return np.ones(labels.shape[0])

        sample_weight = np.array(labels)
        for label, weight in class_weight.items():
            sample_weight[sample_weight == label] = weight
        return sample_weight

    @abstractmethod
    def _get_optimizer_loss(self,):
        """
        return the loss tensor that the optimizer wants to minimize
        :return:
        """
    @abstractmethod
    def _build_graph(self):
        """
        该方法必须在子类的初始化方法末尾被调用
        子类的方法在默认图中构建计算图
        with self.graph.as_default():  # with tf.device("/gpu:0")::
            #构建计算图
            #...
            #...
        """
        raise NotImplementedError

    def compile(self, optimizer='sgd', loss='logloss', metrics=None, loss_weights=None, sample_weight_mode=None, only_init_new=False,):
        """
        compile the model with optimizer and loss function
        :param optimizer:str or predefined optimizer in tensorflow
        ['sgd','adam','adagrad','rmsprop','moment','ftrl']
        :param loss: str  not used
        :param metrics: str ['logloss','mse','mean_squared_error','logloss_with_logits']
        :param loss_weights:
        :param sample_weight_mode:
        :param only_init_new bool
        :return:
        """
        # TODO: 添加loss
        with self.graph.as_default():  # , tf.device('/cpu:0'):
            # 根据指定的优化器和损失函数初始化
            self.metric_list = self._create_metrics(metrics)  # 创建度量列表
            # for the use of BN,tf.get_collection get default Graph
            update_ops = self.graph.get_collection(tf.GraphKeys.UPDATE_OPS)
            with tf.control_dependencies(update_ops):  # for the use of BN
                self.op = self._create_optimizer(optimizer)
                self.optimizer = self.op.minimize(
                    self._get_optimizer_loss(), global_step=self.global_step)  # 创建优化器
                # 执行初始化操作
            self.saver = tf.train.Saver()  # saver要定义在所有变量定义结束之后，且在计算图中
            if only_init_new is False:
                print("init all variables")
                init_op = tf.global_variables_initializer()
            else:
                print("init new variables")
                init_op = new_variable_initializer(
                    self.sess)  # 如果更换了优化器，需要重新初始化一些变量
            self.sess.run(init_op)

    def _create_metrics(self, metric):
        """
        返回每个样本上的评分
        """
        if metric is None:  # 若不指定，则以训练时的损失函数作为度量
            return [self._get_optimizer_loss()]

        if metric not in ['logloss', 'mse', 'mean_squared_error', 'logloss_with_logits']:
            raise ValueError('invalid param metrics')
        # TODO:添加更多度量函数和函数作为参数
        metrics_list = []

        if metric == 'logloss':
            metrics_list.append(sigmoid_cross_entropy_with_probs(
                labels=self._get_input_target(), probs=self._get_output_target()))

        elif metric == 'mse' or metric == 'mean_squared_error':
            metrics_list.append(tf.squared_difference(
                self._get_input_target(), self._get_output_target()))
        elif metric == 'logloss_with_logits':
            metrics_list.append(tf.nn.sigmoid_cross_entropy_with_logits(
                labels=self._get_input_target(), logits=self.logit))
        return metrics_list

    def _create_optimizer(self, optimizer='sgd'):
        """

        :param optimizer: str of optimizer or predefined optimizer in tensorflow
        :return: optimizer object
        """

        optimizer_dict = {'sgd': tf.train.GradientDescentOptimizer(0.01),
                          'adam': tf.train.AdamOptimizer(0.001),
                          'adagrad': tf.train.AdagradOptimizer(0.01),
                          #'adagradda':tf.train.AdagradDAOptimizer(),
                          'rmsprop': tf.train.RMSPropOptimizer(0.001),
                          'moment': tf.train.MomentumOptimizer(0.01, 0.9),
                          'ftrl': tf.train.FtrlOptimizer(0.01)
                          # tf.train.ProximalAdagradOptimizer#padagrad
                          # tf.train.ProximalGradientDescentOptimizer#pgd
                          }
        if isinstance(optimizer, str):
            if optimizer in optimizer_dict.keys():
                return optimizer_dict[optimizer]
            else:
                raise ValueError('invalid optimizer name')
        elif isinstance(optimizer, tf.train.Optimizer):
            return optimizer
        else:
            raise ValueError('invalid parm for optimizer')

    def save_model(self, save_path):
        self.saver.save(self.sess, save_path + '.ckpt', self.global_step)

    def load_model(self, meta_graph_path, ckpt_dir=None, ckpt_path=None):
        """
        :meta_graph_path .meta文件路径
        :ckpt_dir 最新的检查点所在目录
        :ckpt_path 指定检查点
        """
        if ckpt_dir is None and ckpt_path is None:
            raise ValueError('Must specify ckpt_dir or ckpt_path')

        restore_saver = tf.train.import_meta_graph(meta_graph_path, )
        if ckpt_path is None:
            ckpt_path = tf.train.latest_checkpoint(ckpt_dir)
            print(ckpt_path)

        restore_saver.restore(self.sess, ckpt_path)

    def train_on_batch(self, x, y, class_weight=None, sample_weight=None):  # fit a batch
        """
        x: input data, as a Numpy array or list of Numpy arrays (if the model has multiple inputs).
        y: labels, as a Numpy array.
        class_weight: dictionary mapping classes to a weight value, used for scaling the loss function (during training only).
        sample_weight: sample weights, as a Numpy array.
        """
        feed_dict_ = {self._get_input_target(): y, self.train_flag: True,
                      self.sample_weight: self._compute_sample_weight(y, class_weight, sample_weight)}
        input_data = self._get_input_data()
        if isinstance(input_data, list):
            for i in range(len(input_data)):
                feed_dict_[input_data[i]] = x[i]
        else:
            feed_dict_[input_data] = x

        self.sess.run([self.optimizer], feed_dict=feed_dict_)

    def fit(self, x, y, batch_size=1024, epochs=50, validation_split=0.0, validation_data=None,
            val_size=2 ** 18, shuffle=True, initial_epoch=0, min_display=50, max_iter=-1, class_weight=None, sample_weight=None):

        self.class_weight = class_weight
        if class_weight is not None and not isinstance(class_weight, dict):
            raise ValueError('class_weight muse be dict or None')

        if validation_split < 0 or validation_split >= 1:
            raise ValueError(
                "validation_split must be a float number >= 0 and < 1")
        n_samples = get_sample_num(x)
        iters = (n_samples - 1) // batch_size + 1
        self.tr_loss_list = []
        self.val_loss_list = []
        print(iters, "steps per epoch")
        print(batch_size, "samples per step")
        start_time = time.time()
        stop_flag = False
        self.best_loss = np.inf
        self.best_ckpt = None
        if not validation_data and validation_split > 0:
            x, val_x, y, val_y = sklearn_split(
                x, y, test_size=validation_split, random_state=self.seed)
            validation_data = [(val_x, val_y)]

        for i in range(epochs):
            if i < initial_epoch:
                continue
            if shuffle:
                x, y = sklearn_shuffle(x, y, random_state=self.seed)
            for j in range(iters):
                if isinstance(x, list):
                    batch_x = [
                        item[j * batch_size:(j + 1) * batch_size] for item in x]
                else:
                    batch_x = x[j * batch_size:(j + 1) * batch_size]
                batch_y = y[j * batch_size:(j + 1) * batch_size]

                self.train_on_batch(
                    batch_x, batch_y, class_weight, sample_weight)
                if j % min_display == 0:
                    tr_loss = self.evaluate(x, y, val_size, None, None)
                    self.tr_loss_list.append(tr_loss)
                    total_time = time.time() - start_time
                    if validation_data is None:
                        print("Epoch {0: 2d} Step {1: 4d}: tr_loss {2: 0.6f} tr_time {3: 0.1f}".format(i, j, tr_loss,
                                                                                                       total_time))
                    else:
                        val_loss = self.evaluate(
                            validation_data[0][0], validation_data[0][1], val_size)
                        self.val_loss_list.append(val_loss)
                        print(
                            "Epoch {0: 2d} Step {1: 4d}: tr_loss {2: 0.6f} va_loss {3: 0.6f} tr_time {4: 0.1f}".format(
                                i, j, tr_loss, val_loss, total_time))

                        if val_loss < self.best_loss:
                            self.best_loss = val_loss
                            # self.save_model(self.checkpoint_path+'best')

                # self.save_model(self.checkpoint_path)

                if (i * iters) + j == max_iter:
                    stop_flag = True
                    break
            if stop_flag:
                break

    def test_on_batch(self, x, y, class_weight=None, sample_weight=None):
        """
        evaluate sum of batch loss
        """
        feed_dict_ = {self._get_input_target(): y, self.train_flag: False,
                      self.sample_weight: np.ones(y.shape[0])}
        input_data = self._get_input_data()
        if isinstance(input_data, list):
            for i in range(len(input_data)):
                feed_dict_[input_data[i]] = x[i]
        else:
            feed_dict_[input_data] = x
        score = self.sess.run(self.metric_list, feed_dict=feed_dict_)

        if class_weight is None and sample_weight is None:
            return np.mean(score[0])

        sample_weight = self._compute_sample_weight(
            y, class_weight, sample_weight)
        weighted_score = np.mean(score[0] * sample_weight)
        return weighted_score

    def evaluate(self, x, y, val_size=2 ** 18, class_weight=None, sample_weight=None):
        """
        evaluate the model and return mean loss
        :param data: DataFrame
        :param feature_list: list of features
        :param target_str:
        :param val_size:
        :return: mean loss
        """
        val_samples = get_sample_num(x)
        val_iters = (val_samples - 1) // val_size + 1
        total_val_loss = 0
        for i in range(0, val_iters):
            if isinstance(x, list):
                batch_x = [item[i * val_size:(i + 1) * val_size] for item in x]
            else:
                batch_x = x[i * val_size:(i + 1) * val_size]
            batch_y = y[i * val_size:(i + 1) * val_size]
            val_loss = self.test_on_batch(
                batch_x, batch_y, class_weight, sample_weight)
            total_val_loss += val_loss
        return total_val_loss / val_samples

    def predict_on_batch(self, x, ):
        feed_dict_ = {self.train_flag: False}
        input_data = self._get_input_data()
        if isinstance(input_data, list):
            for i in range(len(input_data)):
                feed_dict_[input_data[i]] = x[i]
        else:
            feed_dict_[input_data] = x
        prob = self.sess.run([self._get_output_target()], feed_dict=feed_dict_)
        return prob[0]

    def predict(self, x, batch_size=2 ** 18):
        n_samples = get_sample_num(x)
        iters = (n_samples - 1) // batch_size + 1
        pred_prob = np.array([])
        for j in range(iters):
            if isinstance(x, list):
                batch_x = [
                    item[j * batch_size:(j + 1) * batch_size] for item in x]
            else:
                batch_x = x[j * batch_size:(j + 1) * batch_size]
            batch_prob = self.predict_on_batch(batch_x, )
            pred_prob = np.concatenate((pred_prob, batch_prob))
        return pred_prob